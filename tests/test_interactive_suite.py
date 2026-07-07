import unittest
from unittest.mock import MagicMock, patch
from fastapi.testclient import TestClient
from PIL import Image
import io

from backend.main import app
from backend.ocr_engine import PageResult, PageResponse

class TestInteractiveSuite(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(app)

    @patch("backend.ocr_engine.subprocess.run")
    @patch("backend.ocr_engine.get_pdf_media_box_width_height")
    def test_crop_and_filter_pdf_page(self, mock_box, mock_run):
        mock_box.return_value = (500.0, 700.0)
        
        # Create a mock transparent PNG image
        img = Image.new("RGBA", (100, 100), (255, 0, 0, 255))
        img_bytes = io.BytesIO()
        img.save(img_bytes, format="PNG")
        
        mock_run.return_value = MagicMock(returncode=0, stdout=img_bytes.getvalue())
        
        from backend.ocr_engine import crop_and_filter_pdf_page
        result = crop_and_filter_pdf_page(
            local_pdf_path="dummy.pdf",
            page_num=1,
            x=0.1,
            y=0.2,
            width=0.5,
            height=0.4,
            target_longest_image_dim=768,
            binarize=False,
            high_contrast=False,
            despeckle=False
        )
        self.assertTrue(len(result) > 0)

    @patch("backend.main.resolve_within_base")
    @patch("backend.main.job_manager.get_job")
    @patch("backend.main.load_settings")
    @patch("backend.main.get_upload_dir")
    @patch("backend.main.process_cropped_zone")
    def test_reprocess_zone_endpoint(self, mock_process_zone, mock_upload_dir, mock_settings, mock_get_job, mock_resolve):
        # Setup mocks
        mock_job = MagicMock()
        mock_job.pdf_filename = "dummy.pdf"
        mock_job.page_confidence = {}
        mock_job.page_token_logprobs = {}
        mock_get_job.return_value = mock_job

        mock_settings.return_value = {}

        # Mock upload path + containment guard (validated path with exists() True)
        mock_path = MagicMock()
        mock_path.exists.return_value = True
        mock_upload_dir.return_value = mock_path
        mock_resolve.return_value = mock_path
        
        # Mock process_cropped_zone return
        mock_page_result = PageResult(
            page_num=1,
            response=PageResponse(
                primary_language="zh",
                is_rotation_valid=True,
                rotation_correction=0,
                is_table=False,
                is_diagram=False,
                natural_text="cropped OCR result"
            ),
            success=True,
            confidence_score=92.5,
            token_logprobs=[{"token": "cropped", "logprob": -0.05, "confidence": 95.1}]
        )
        mock_process_zone.return_value = mock_page_result
        
        response = self.client.post(
            "/api/jobs/reprocess-zone",
            json={
                "job_id": "test-job-id",
                "page_num": 1,
                "x": 0.1,
                "y": 0.2,
                "width": 0.5,
                "height": 0.4
            }
        )
        
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["natural_text"], "cropped OCR result")
        self.assertEqual(data["confidence_score"], 92.5)
        self.assertEqual(data["token_logprobs"][0]["token"], "cropped")
        
        # Verify job state updated
        self.assertEqual(mock_job.page_confidence["1"], 92.5)
        self.assertEqual(mock_job.page_token_logprobs["1"][0]["token"], "cropped")

    @patch("backend.main.get_output_dir")
    @patch("backend.main.resolve_within_base")
    def test_save_edited_file_endpoint(self, mock_resolve, mock_output_dir):
        # The path-containment helper is exercised directly in test_security.py;
        # here we mock it to a deterministic path and assert the write contract.
        # get_output_dir is mocked so it never reads the real settings.json.
        mock_output_dir.return_value = MagicMock()
        fake_path = MagicMock()
        fake_path.parent.exists.return_value = True
        mock_resolve.return_value = fake_path

        with patch("builtins.open", unittest.mock.mock_open()) as mock_file:
            response = self.client.put(
                "/api/download/dummy_FULL.md",
                json={"content": "edited content"}
            )
            self.assertEqual(response.status_code, 200)
            mock_file.assert_called_once_with(fake_path, "w", encoding="utf-8")
            mock_file().write.assert_called_once_with("edited content")
