import unittest
from unittest.mock import patch, MagicMock
import io
import base64
from PIL import Image
from backend.ocr_engine import generate_toc, render_pdf_to_base64png

class TestTOCGeneration(unittest.TestCase):
    def test_generate_toc_basic(self):
        md = "## Introduction\nSome text\n## Method\nMore text"
        result = generate_toc(md)
        self.assertIn("# Table of Contents", result)
        self.assertIn("- [Introduction](#introduction)", result)
        self.assertIn("- [Method](#method)", result)
        self.assertTrue(result.endswith("## Method\nMore text"))

    def test_generate_toc_unicode(self):
        md = "## 第一章 乾坤\n一些文字\n## 第二章 屯蒙\n更多文字"
        result = generate_toc(md)
        self.assertIn("# Table of Contents", result)
        # re.sub(r"[^\w\s-]", "", slug) retains Chinese chars
        self.assertIn("- [第一章 乾坤](#第一章-乾坤)", result)
        self.assertIn("- [第二章 屯蒙](#第二章-屯蒙)", result)

    def test_generate_toc_no_headers(self):
        md = "# Main Title\nSome text\n### Subsection\nMore text"
        result = generate_toc(md)
        self.assertNotIn("# Table of Contents", result)
        self.assertEqual(result, md)


class TestImageFilters(unittest.TestCase):
    @patch("backend.ocr_engine.get_pdf_media_box_width_height")
    @patch("backend.ocr_engine.subprocess.run")
    def test_render_with_filters(self, mock_run, mock_dims):
        # Setup dims mock
        mock_dims.return_value = (612, 792)
        
        # Setup pdftoppm subprocess output (a tiny solid gray PNG)
        img = Image.new("RGB", (10, 10), color="gray")
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        mock_process = MagicMock()
        mock_process.returncode = 0
        mock_process.stdout = buf.getvalue()
        mock_run.return_value = mock_process
        
        # Execute render with filters
        b64_out = render_pdf_to_base64png(
            "dummy.pdf", page_num=1, target_longest_image_dim=10,
            binarize=True, high_contrast=True, despeckle=True
        )
        
        # Decode and check that it's a valid base64 image
        img_bytes = base64.b64decode(b64_out)
        img_out = Image.open(io.BytesIO(img_bytes))
        self.assertEqual(img_out.size, (10, 10))
        # Binarization converts it to '1' mode (1-bit pixels)
        self.assertEqual(img_out.mode, '1')


class TestJobTelemetryAggregation(unittest.IsolatedAsyncioTestCase):
    @patch("backend.job_manager.process_pdf_to_markdown")
    @patch("pypdf.PdfReader")
    async def test_job_telemetry_aggregation(self, mock_reader, mock_process_pdf):
        from backend.job_manager import JobManager
        from backend.models import JobStatus

        # Mock PdfReader page count
        mock_pdf = MagicMock()
        mock_pdf.pages = [MagicMock()] * 2
        mock_reader.return_value = mock_pdf

        # Mock ocr engine return
        mock_process_pdf.return_value = {
            "output_path": "dummy_FULL.md",
            "output_filename": "dummy_FULL.md",
            "total_pages": 2,
            "pages_completed": 2,
            "pages_failed": 0,
            "duration_seconds": 12.5,
            "average_confidence": 95.2,
            "total_retries": 1,
        }

        # Initialize manager and run mock job
        manager = JobManager()
        await manager.start()
        
        job_id = await manager.create_job("dummy.pdf", "dummy.pdf", {})
        
        # Wait slightly or execute run directly
        await manager._run_job(job_id, "dummy.pdf", {})
        
        # Verify job object is updated
        job = manager.get_job(job_id)
        self.assertIsNotNone(job)
        self.assertEqual(job.status, JobStatus.COMPLETED)
        self.assertEqual(job.total_runtime, 12.5)
        self.assertEqual(job.average_confidence, 95.2)
        self.assertEqual(job.total_retries, 1)

        # Stop manager background tasks to prevent warning
        if hasattr(manager, "_worker_task"):
            manager._worker_task.cancel()
        if hasattr(manager, "_startup_check_task"):
            manager._startup_check_task.cancel()
