import unittest
from unittest.mock import AsyncMock, patch, MagicMock
import base64
import io
import math
from PIL import Image

from backend.ocr_engine import build_prompt, process_single_page, PageResult, PageResponse

# Create a mock base64 image (small red dot)
def get_mock_base64_image():
    img = Image.new("RGB", (10, 10), color="red")
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return base64.b64encode(buf.getvalue()).decode("utf-8")

class TestLogicLayer(unittest.IsolatedAsyncioTestCase):
    def test_build_prompt_with_context(self):
        prompt = build_prompt(previous_page_context="Hello World")
        self.assertIn("trailing text: [ Hello World ]", prompt)
        self.assertIn("Do not repeat the context", prompt)

    @patch("backend.ocr_engine.build_page_query")
    @patch("backend.ocr_engine.render_pdf_to_base64png")
    async def test_auto_rotate_correction(self, mock_render, mock_build_query):
        mock_base64 = get_mock_base64_image()
        mock_render.return_value = mock_base64
        
        # We need mock_build_query to return a valid query with our base64 image url
        # inside it, so the rotation code can extract it.
        mock_query_side_effect = lambda *args, **kwargs: {
            "model": "test-model",
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": "prompt"},
                        {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{kwargs.get('override_image_base64') or mock_base64}"}},
                    ]
                }
            ]
        }
        mock_build_query.side_effect = mock_query_side_effect

        # Mock http response
        mock_client = MagicMock()
        mock_response_1 = MagicMock()
        mock_response_1.status_code = 200
        # First response returns is_rotation_valid = False
        mock_response_1.json.return_value = {
            "choices": [{
                "finish_reason": "stop",
                "message": {
                    "content": "---\nis_rotation_valid: false\nrotation_correction: 90\n---\nSome text"
                },
                "logprobs": {
                    "content": [{"logprob": 0.0}] # 100% confidence
                }
            }],
            "usage": {"prompt_tokens": 10, "completion_tokens": 10}
        }

        mock_response_2 = MagicMock()
        mock_response_2.status_code = 200
        # Second response returns is_rotation_valid = True
        mock_response_2.json.return_value = {
            "choices": [{
                "finish_reason": "stop",
                "message": {
                    "content": "---\nis_rotation_valid: true\nrotation_correction: 0\n---\nRotated text"
                },
                "logprobs": {
                    "content": [{"logprob": 0.0}]
                }
            }],
            "usage": {"prompt_tokens": 10, "completion_tokens": 10}
        }

        mock_client.post = AsyncMock()
        mock_client.post.side_effect = [mock_response_1, mock_response_2]

        result = await process_single_page(
            mock_client,
            pdf_path="dummy.pdf",
            page_num=1,
            server_url="http://localhost:1234",
            model="test-model",
            target_dim=100,
            max_tokens=1000,
            max_retries=2
        )

        self.assertTrue(result.success)
        self.assertEqual(result.response.natural_text, "Rotated text")
        # Assert that client.post was called twice (initial + retry)
        self.assertEqual(mock_client.post.call_count, 2)

    @patch("backend.ocr_engine.build_page_query")
    @patch("backend.ocr_engine.render_pdf_to_base64png")
    async def test_auto_upscale_table(self, mock_render, mock_build_query):
        mock_base64 = get_mock_base64_image()
        mock_render.return_value = mock_base64
        
        captured_dims = []
        def build_query_side_effect(*args, **kwargs):
            captured_dims.append(args[2]) # target_dim is the 3rd positional arg
            return {
                "model": "test-model",
                "messages": [
                    {
                        "role": "user",
                        "content": [
                            {"type": "text", "text": "prompt"},
                            {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{mock_base64}"}},
                        ]
                    }
                ]
            }
        mock_build_query.side_effect = build_query_side_effect

        mock_client = MagicMock()
        mock_response_1 = MagicMock()
        mock_response_1.status_code = 200
        # First response is a table
        mock_response_1.json.return_value = {
            "choices": [{
                "finish_reason": "stop",
                "message": {
                    "content": "---\nis_table: true\n---\nTable data"
                },
                "logprobs": {
                    "content": [{"logprob": 0.0}]
                }
            }],
            "usage": {"prompt_tokens": 10, "completion_tokens": 10}
        }

        mock_response_2 = MagicMock()
        mock_response_2.status_code = 200
        # Second response succeeds
        mock_response_2.json.return_value = {
            "choices": [{
                "finish_reason": "stop",
                "message": {
                    "content": "---\nis_table: true\n---\nUpscaled table data"
                },
                "logprobs": {
                    "content": [{"logprob": 0.0}]
                }
            }],
            "usage": {"prompt_tokens": 10, "completion_tokens": 10}
        }

        mock_client.post = AsyncMock()
        mock_client.post.side_effect = [mock_response_1, mock_response_2]

        result = await process_single_page(
            mock_client,
            pdf_path="dummy.pdf",
            page_num=1,
            server_url="http://localhost:1234",
            model="test-model",
            target_dim=768,
            max_tokens=1000,
            max_retries=2
        )

        self.assertTrue(result.success)
        self.assertEqual(result.response.natural_text, "Upscaled table data")
        self.assertEqual(mock_client.post.call_count, 2)
        # Verify target dimension was upscaled to 2048 in the second attempt
        self.assertEqual(captured_dims, [768, 2048])

    @patch("backend.ocr_engine.build_page_query")
    @patch("backend.ocr_engine.render_pdf_to_base64png")
    async def test_smart_retry_low_confidence(self, mock_render, mock_build_query):
        mock_base64 = get_mock_base64_image()
        mock_render.return_value = mock_base64
        
        captured_dims = []
        def build_query_side_effect(*args, **kwargs):
            captured_dims.append(args[2])
            return {
                "model": "test-model",
                "messages": [
                    {
                        "role": "user",
                        "content": [
                            {"type": "text", "text": "prompt"},
                            {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{mock_base64}"}},
                        ]
                    }
                ]
            }
        mock_build_query.side_effect = build_query_side_effect

        mock_client = MagicMock()
        mock_response_1 = MagicMock()
        mock_response_1.status_code = 200
        # First response has low confidence (logprob = -0.5 -> confidence = e^-0.5 * 100 = 60.6%)
        mock_response_1.json.return_value = {
            "choices": [{
                "finish_reason": "stop",
                "message": {
                    "content": "---\nis_table: false\n---\nLow confidence text"
                },
                "logprobs": {
                    "content": [{"logprob": -0.5}]
                }
            }],
            "usage": {"prompt_tokens": 10, "completion_tokens": 10}
        }

        mock_response_2 = MagicMock()
        mock_response_2.status_code = 200
        # Second response succeeds with high confidence
        mock_response_2.json.return_value = {
            "choices": [{
                "finish_reason": "stop",
                "message": {
                    "content": "---\nis_table: false\n---\nHigh confidence text"
                },
                "logprobs": {
                    "content": [{"logprob": 0.0}]
                }
            }],
            "usage": {"prompt_tokens": 10, "completion_tokens": 10}
        }

        mock_client.post = AsyncMock()
        mock_client.post.side_effect = [mock_response_1, mock_response_2]

        result = await process_single_page(
            mock_client,
            pdf_path="dummy.pdf",
            page_num=1,
            server_url="http://localhost:1234",
            model="test-model",
            target_dim=1000,
            max_tokens=1000,
            max_retries=2
        )

        self.assertTrue(result.success)
        self.assertEqual(result.response.natural_text, "High confidence text")
        self.assertEqual(mock_client.post.call_count, 2)
        # Verify target dimension was scaled by 1.25x: 1000 -> 1250
        self.assertEqual(captured_dims, [1000, 1250])

    @patch("backend.ocr_engine.render_pdf_to_base64png")
    async def test_build_page_query_rtl_no_rotation(self, mock_render):
        # Create a non-square mock image (e.g., 20x10) to make sure it is not rotated (stays 20x10)
        img = Image.new("RGB", (20, 10), color="blue")
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        mock_base64 = base64.b64encode(buf.getvalue()).decode("utf-8")
        mock_render.return_value = mock_base64

        from backend.ocr_engine import build_page_query
        
        # Call build_page_query with reading_direction="Vertical RTL"
        query = await build_page_query(
            local_pdf_path="dummy.pdf",
            page=1,
            target_longest_image_dim=20,
            model_name="test-model",
            reading_direction="Vertical RTL"
        )
        
        # Verify the returned image was not rotated (remains 20x10)
        img_url = query["messages"][0]["content"][1]["image_url"]["url"]
        output_base64 = img_url.split("base64,")[1]
        output_img_bytes = base64.b64decode(output_base64)
        output_img = Image.open(io.BytesIO(output_img_bytes))
        
        self.assertEqual(output_img.size, (20, 10))

if __name__ == "__main__":
    unittest.main()
