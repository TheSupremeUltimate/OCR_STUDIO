"""
OCR Studio — Security regression tests (Phase 4.13)

Covers the audit v1 remediations:
  F-01  Path traversal containment on file-accessing routes
  F-02  CORS lockdown (no wildcard origin)
  F-03  Typed SaveFileRequest body (validation, not raw dict)
  F-04  document_structure no longer clobbers saved settings

These use real temp directories so the containment logic is exercised
end-to-end rather than mocked.
"""

import tempfile
import unittest
import unittest.mock
from pathlib import Path

from fastapi.testclient import TestClient
from starlette.middleware.cors import CORSMiddleware

import backend.main as main
from backend.main import app
from backend.config import resolve_within_base
from backend.models import JobCreateRequest, SaveFileRequest


class TestPathContainmentHelper(unittest.TestCase):
    """Unit tests for the centralized resolve_within_base() guard."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.base = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def test_legit_flat_filename_allowed(self):
        result = resolve_within_base(self.base, "doc_FULL.md")
        self.assertEqual(result, (self.base / "doc_FULL.md").resolve())

    def test_backslash_traversal_rejected(self):
        with self.assertRaises(ValueError):
            resolve_within_base(self.base, "..\\..\\secret.txt")

    def test_forward_slash_traversal_rejected(self):
        with self.assertRaises(ValueError):
            resolve_within_base(self.base, "../../secret.txt")

    def test_absolute_path_injection_rejected(self):
        with self.assertRaises(ValueError):
            resolve_within_base(self.base, "C:\\Windows\\win.ini")

    def test_empty_filename_rejected(self):
        with self.assertRaises(ValueError):
            resolve_within_base(self.base, "")


class TestDownloadRouteContainment(unittest.TestCase):
    """F-01: the download routes must reject traversal with HTTP 400."""

    def setUp(self):
        self.client = TestClient(app)
        self._tmp = tempfile.TemporaryDirectory()
        self.out_dir = Path(self._tmp.name)
        # Point the app's output dir at our sandbox.
        self._patcher = unittest.mock.patch(
            "backend.main.get_output_dir", return_value=self.out_dir
        )
        self._patcher.start()
        # A "secret" file living OUTSIDE the sandbox (in its parent).
        self.secret = self.out_dir.parent / "phase413_secret.txt"
        self.secret.write_text("TOP SECRET", encoding="utf-8")

    def tearDown(self):
        self._patcher.stop()
        if self.secret.exists():
            self.secret.unlink()
        self._tmp.cleanup()

    def test_get_backslash_traversal_rejected(self):
        # Pre-fix this returned 200 with the secret's contents.
        resp = self.client.get("/api/download/..%5Cphase413_secret.txt")
        self.assertEqual(resp.status_code, 400)
        self.assertNotIn("TOP SECRET", resp.text)

    def test_get_absolute_path_rejected(self):
        resp = self.client.get("/api/download/C:%5CWindows%5Cwin.ini")
        self.assertEqual(resp.status_code, 400)

    def test_put_traversal_rejected_and_no_write(self):
        target = self.out_dir.parent / "phase413_evil.md"
        self.assertFalse(target.exists())
        resp = self.client.put(
            "/api/download/..%5Cphase413_evil.md",
            json={"content": "malicious"},
        )
        self.assertEqual(resp.status_code, 400)
        self.assertFalse(target.exists(), "traversal PUT must not create a file outside the base")

    def test_legit_round_trip_write_then_read(self):
        # Guards against over-blocking / auto-save regression.
        resp = self.client.put(
            "/api/download/roundtrip_FULL.md",
            json={"content": "hello 世界"},
        )
        self.assertEqual(resp.status_code, 200)
        self.assertTrue((self.out_dir / "roundtrip_FULL.md").exists())

        resp2 = self.client.get("/api/download/roundtrip_FULL.md")
        self.assertEqual(resp2.status_code, 200)
        self.assertIn("hello 世界", resp2.text)


class TestSaveFileRequestValidation(unittest.TestCase):
    """F-03: PUT body must be a typed model, not a raw dict."""

    def setUp(self):
        self.client = TestClient(app)

    def test_missing_content_is_422(self):
        # Body validation happens before the handler runs.
        resp = self.client.put("/api/download/whatever_FULL.md", json={})
        self.assertEqual(resp.status_code, 422)

    def test_model_requires_content(self):
        with self.assertRaises(Exception):
            SaveFileRequest()  # type: ignore[call-arg]


class TestCorsLockdown(unittest.TestCase):
    """F-02: no wildcard origin may remain in the CORS middleware."""

    def test_no_wildcard_origin(self):
        cors = [m for m in app.user_middleware if m.cls is CORSMiddleware]
        self.assertTrue(cors, "CORS middleware should be configured")
        kwargs = getattr(cors[0], "kwargs", {})
        origins = kwargs.get("allow_origins", [])
        self.assertNotIn("*", origins)
        self.assertIn("http://localhost:8080", origins)
        # Credentials must not be enabled (no cookies are used).
        self.assertFalse(kwargs.get("allow_credentials", False))


class TestDocumentStructureDefault(unittest.TestCase):
    """F-04: document_structure default must not clobber saved settings."""

    def test_default_is_none(self):
        req = JobCreateRequest(pdf_filename="x.pdf")
        self.assertIsNone(req.document_structure)

    def test_omitted_field_excluded_from_overrides(self):
        req = JobCreateRequest(pdf_filename="x.pdf")
        overrides = req.model_dump(exclude={"pdf_filename"}, exclude_none=True)
        self.assertNotIn("document_structure", overrides)

    def test_explicit_value_still_passes_through(self):
        req = JobCreateRequest(
            pdf_filename="x.pdf",
            document_structure="Main Text + Interline Commentary",
        )
        overrides = req.model_dump(exclude={"pdf_filename"}, exclude_none=True)
        self.assertEqual(
            overrides["document_structure"], "Main Text + Interline Commentary"
        )


if __name__ == "__main__":
    unittest.main()
