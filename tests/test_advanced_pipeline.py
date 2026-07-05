import unittest
from unittest.mock import MagicMock, patch, AsyncMock
from fastapi.testclient import TestClient

from backend.main import app
from backend.ocr_engine import (
    align_strings,
    vote_consensus,
    merge_overlapping_texts,
    PageResult,
    PageResponse,
)

class TestAdvancedPipeline(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(app)

    def test_align_strings(self):
        # Test basic gap alignment
        ref = "ABCDEF"
        s = "ABXDEF"
        ref_a, s_a = align_strings(ref, s)
        self.assertEqual(len(ref_a), len(s_a))
        
        # Test deletion alignment
        ref = "ABCDEF"
        s = "ABDEF"
        ref_a, s_a = align_strings(ref, s)
        self.assertEqual(ref_a, "ABCDEF")
        self.assertEqual(s_a, "AB-DEF")

    def test_vote_consensus(self):
        # 1. Standard majority vote
        s1 = "This is test text."
        s2 = "Th1s is test text."
        s3 = "This is test text."
        result = vote_consensus(s1, s2, s3)
        self.assertEqual(result, "This is test text.")

        # 2. Complete voting consensus (different strings)
        s1 = "Hello World"
        s2 = "Hella World"
        s3 = "Hello Warld"
        result = vote_consensus(s1, s2, s3)
        self.assertEqual(result, "Hello World")

        # 3. Handle deletions/gaps
        s1 = "ABCDEF"
        s2 = "AB-DEF"
        s3 = "ABCDEF"
        result = vote_consensus(s1, s2, s3)
        self.assertEqual(result, "ABCDEF")

    def test_merge_overlapping_texts(self):
        # 1. Standard overlap matching
        top_text = "This is the first paragraph. Here is some overlapping"
        bottom_text = "paragraph. Here is some overlapping content on the bottom."
        merged = merge_overlapping_texts(top_text, bottom_text)
        self.assertEqual(
            merged,
            "This is the first paragraph. Here is some overlapping content on the bottom."
        )

        # 2. No overlap match fallback
        top_text = "Hello World"
        bottom_text = "Goodbye World"
        merged = merge_overlapping_texts(top_text, bottom_text)
        self.assertEqual(merged, "Hello World\n\nGoodbye World")

    @patch("backend.main.load_settings")
    @patch("httpx.AsyncClient.post")
    def test_translate_endpoint(self, mock_post, mock_settings):
        # Setup mocks
        mock_settings.return_value = {
            "server_url": "http://localhost:1234/v1",
            "model": "allenai_olmocr-2-7b-1025"
        }
        
        mock_res = MagicMock()
        mock_res.status_code = 200
        mock_res.json.return_value = {
            "choices": [{
                "message": {
                    "content": "English translation result"
                }
            }]
        }
        mock_post.return_value = mock_res

        response = self.client.post(
            "/api/jobs/translate",
            json={"content": "Classical Chinese text content"}
        )
        
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["translated_text"], "English translation result")

    @patch("backend.ocr_engine.process_single_page", new_callable=AsyncMock)
    def test_run_consensus_ocr(self, mock_process_single):
        # Mock the three runs of process_single_page
        mock_r1 = PageResult(
            page_num=1,
            success=True,
            confidence_score=80.0,
            response=PageResponse(primary_language="zh", is_rotation_valid=True, rotation_correction=0, is_table=False, is_diagram=False, natural_text="Hello World"),
            token_logprobs=[]
        )
        mock_r2 = PageResult(
            page_num=1,
            success=True,
            confidence_score=90.0,
            response=PageResponse(primary_language="zh", is_rotation_valid=True, rotation_correction=0, is_table=False, is_diagram=False, natural_text="Hella World"),
            token_logprobs=[]
        )
        mock_r3 = PageResult(
            page_num=1,
            success=True,
            confidence_score=85.0,
            response=PageResponse(primary_language="zh", is_rotation_valid=True, rotation_correction=0, is_table=False, is_diagram=False, natural_text="Hello World"),
            token_logprobs=[]
        )
        mock_process_single.side_effect = [mock_r1, mock_r2, mock_r3]

        from backend.ocr_engine import run_consensus_ocr
        import httpx
        import asyncio

        async def run_test():
            async with httpx.AsyncClient() as client:
                return await run_consensus_ocr(
                    client, "dummy.pdf", 1, "http://localhost:1234/v1", "model", 1000, 1,
                    "", False, "Default", "", "Standard", False, False, False
                )

        result = asyncio.run(run_test())
        self.assertTrue(result.success)
        self.assertEqual(result.response.natural_text, "Hello World")
        self.assertEqual(result.confidence_score, 85.0)  # average of 80, 90, 85

    @patch("backend.ocr_engine.crop_top_bottom_horizontal")
    @patch("backend.ocr_engine.process_single_page", new_callable=AsyncMock)
    def test_run_adaptive_density_chunking(self, mock_process_single, mock_crop):
        mock_crop.return_value = ("top_b64", "bottom_b64")
        
        # Mock top segment run (succeeds) and bottom segment run (succeeds)
        mock_top = PageResult(
            page_num=1,
            success=True,
            confidence_score=90.0,
            response=PageResponse(primary_language="zh", is_rotation_valid=True, rotation_correction=0, is_table=False, is_diagram=False, natural_text="Top segment content overlapping"),
            token_logprobs=[]
        )
        mock_bottom = PageResult(
            page_num=1,
            success=True,
            confidence_score=80.0,
            response=PageResponse(primary_language="zh", is_rotation_valid=True, rotation_correction=0, is_table=False, is_diagram=False, natural_text="overlapping bottom segment content"),
            token_logprobs=[]
        )
        mock_process_single.side_effect = [mock_top, mock_bottom]

        from backend.ocr_engine import run_adaptive_density_chunking
        import httpx
        import asyncio

        async def run_test():
            async with httpx.AsyncClient() as client:
                return await run_adaptive_density_chunking(
                    client, "dummy.pdf", 1, "http://localhost:1234/v1", "model", 768, 1000, 1,
                    "", False, "Default", "", "Standard", False, False, False
                )

        result = asyncio.run(run_test())
        self.assertTrue(result.success)
        self.assertEqual(result.response.natural_text, "Top segment content overlapping bottom segment content")
        self.assertEqual(result.confidence_score, 85.0)  # average of 90 and 80
        self.assertTrue(result.is_fallback)

