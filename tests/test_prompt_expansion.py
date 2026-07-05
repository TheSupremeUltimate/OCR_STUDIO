import unittest
import json
from pathlib import Path
from backend.ocr_engine import build_prompt
from backend.models import SettingsUpdateRequest, SettingsResponse, JobCreateRequest
from backend.config import load_settings, save_settings, SETTINGS_FILE, DEFAULTS

class TestPromptExpansion(unittest.TestCase):
    def setUp(self):
        # Backup settings.json if it exists
        self.settings_backup = None
        if SETTINGS_FILE.exists():
            try:
                self.settings_backup = SETTINGS_FILE.read_text(encoding="utf-8")
            except Exception:
                pass

    def tearDown(self):
        # Restore settings.json backup
        if self.settings_backup is not None:
            try:
                SETTINGS_FILE.write_text(self.settings_backup, encoding="utf-8")
            except Exception:
                pass
        elif SETTINGS_FILE.exists():
            try:
                SETTINGS_FILE.unlink()
            except Exception:
                pass

    def test_build_prompt_default(self):
        prompt = build_prompt()
        self.assertIn("Attached is one page of a document that you must process", prompt)
        # Verify no additions are appended by default
        self.assertNotIn("This text contains document-specific terms", prompt)
        self.assertNotIn("Do not modernize characters", prompt)
        self.assertNotIn("traditional woodblock print text", prompt)

    def test_build_prompt_custom_glossary(self):
        prompt = build_prompt(custom_glossary="I Ching, Hexagram")
        self.assertIn("Attached is one page of a document that you must process", prompt)
        self.assertIn("This text contains document-specific terms/proper nouns. Prioritize matching these sequences visually: I Ching, Hexagram", prompt)
        self.assertNotIn("Do not modernize characters", prompt)

    def test_build_prompt_strict_mode(self):
        prompt = build_prompt(strict_mode=True)
        self.assertIn("Do not modernize characters, do not correct perceived historical typos, and do not fill in gaps.", prompt)

    def test_build_prompt_reading_direction_rtl(self):
        prompt = build_prompt(reading_direction="Vertical RTL")
        self.assertIn("This is a traditional Chinese document read vertically from right to left.", prompt)

    def test_build_prompt_reading_direction_ltr(self):
        prompt = build_prompt(reading_direction="Horizontal LTR")
        self.assertIn("This text should be read horizontally from left to right, top to bottom.", prompt)

    def test_build_prompt_document_structure(self):
        prompt = build_prompt(document_structure="Main Text + Interline Commentary")
        self.assertIn("features large main text and small interline commentary", prompt)
        self.assertIn("wrap it entirely in [brackets]", prompt)

    def test_build_prompt_combined(self):
        prompt = build_prompt(
            custom_glossary="test_term",
            strict_mode=True,
            reading_direction="Vertical RTL"
        )
        self.assertIn("test_term", prompt)
        self.assertIn("Do not modernize characters", prompt)
        self.assertIn("This is a traditional Chinese document read vertically from right to left.", prompt)

    def test_pydantic_schemas(self):
        # Verify JobCreateRequest
        job_req = JobCreateRequest(
            pdf_filename="test.pdf",
            document_structure="Main Text + Interline Commentary"
        )
        self.assertEqual(job_req.pdf_filename, "test.pdf")
        self.assertEqual(job_req.document_structure, "Main Text + Interline Commentary")

        # Verify SettingsUpdateRequest
        req = SettingsUpdateRequest(
            custom_glossary="my_glossary",
            strict_mode=True,
            reading_direction="Vertical RTL",
            document_structure="Main Text + Interline Commentary"
        )
        self.assertEqual(req.custom_glossary, "my_glossary")
        self.assertEqual(req.strict_mode, True)
        self.assertEqual(req.reading_direction, "Vertical RTL")
        self.assertEqual(req.document_structure, "Main Text + Interline Commentary")

        # Verify SettingsResponse
        res = SettingsResponse(
            server_url="http://localhost:1234",
            model="test-model",
            workers=2,
            pages_per_group=5,
            target_longest_image_dim=1024,
            max_page_retries=1,
            max_tokens=2000,
            output_dir="some_dir",
            page_range="1-3",
            custom_glossary="test",
            strict_mode=False,
            reading_direction="Horizontal LTR",
            document_structure="Standard",
            binarize=False,
            high_contrast=False,
            despeckle=False
        )
        self.assertEqual(res.custom_glossary, "test")
        self.assertEqual(res.strict_mode, False)
        self.assertEqual(res.reading_direction, "Horizontal LTR")
        self.assertEqual(res.document_structure, "Standard")

    def test_config_persistence(self):
        # Clean state
        if SETTINGS_FILE.exists():
            try:
                SETTINGS_FILE.unlink()
            except Exception:
                pass
        
        # Load settings should yield default settings
        settings = load_settings()
        self.assertEqual(settings["custom_glossary"], "")
        self.assertEqual(settings["strict_mode"], False)
        self.assertEqual(settings["reading_direction"], "Default")
        self.assertEqual(settings["document_structure"], "Standard")

        # Save settings
        settings["custom_glossary"] = "updated_glossary"
        settings["strict_mode"] = True
        settings["reading_direction"] = "Vertical RTL"
        settings["document_structure"] = "Main Text + Interline Commentary"
        save_settings(settings)

        # Reload settings
        reloaded = load_settings()
        self.assertEqual(reloaded["custom_glossary"], "updated_glossary")
        self.assertEqual(reloaded["strict_mode"], True)
        self.assertEqual(reloaded["reading_direction"], "Vertical RTL")
        self.assertEqual(reloaded["document_structure"], "Main Text + Interline Commentary")

if __name__ == "__main__":
    unittest.main()
