"""
OCR Studio — Phase 4.14 regression baseline for page_008.pdf (Cheng-Zhu 《周易》).

Two layers:

* Offline (always-on) tests validate the deterministic pipeline layers against
  the ground-truth transcript without any network:
    - glossary preset loading + prompt injection (the Phase-4.8 hook),
    - 3-way consensus alignment recovering the 本→木 and 晝→畫 OCR errors,
    - horizontal overlap stitching reconstructing a split of the transcript.

* A live benchmark (TestPage008LiveOCR) processes the real PDF through LM Studio
  and compares to the ground truth. It is skipped when the server is unreachable
  so the standard offline suite stays green.

The confusable characters under test:
    本 (root/origin)  vs  木 (tree)      — 本義 is Zhu Xi's "Original Meaning" marker
    晝 (daytime)      vs  畫 (stroke)    — 晝夜 = "day and night"
(Note 剛柔之畫 legitimately uses 畫, so we assert on the binome 晝夜, not the glyph.)
"""

import difflib
import unittest
from pathlib import Path

import httpx

from backend.config import (
    load_glossary_terms,
    list_glossaries,
    load_settings,
)
from backend.ocr_engine import (
    build_prompt,
    parse_glossary,
    apply_corrections,
    vote_consensus,
    merge_overlapping_texts,
)

FIXTURE = Path(__file__).parent / "fixtures" / "page_008_ground_truth.md"
GROUND_TRUTH = FIXTURE.read_text(encoding="utf-8").strip()

PDF_PATH = Path(__file__).parent.parent / "docs" / "TEST_PDFs" / "page_008.pdf"
GLOSSARY_NAME = "cheng_zhu"

GLOSSARY_TERMS = ["本義", "晝夜", "朱子曰", "臨川吳氏曰", "節齋蔡氏曰", "雲峯胡氏曰"]


def _lm_studio_available() -> bool:
    """Return True if the configured LM Studio server answers /models quickly."""
    try:
        url = load_settings().get("server_url", "").rstrip("/")
        if not url:
            return False
        return httpx.get(f"{url}/models", timeout=2.0).status_code == 200
    except Exception:
        return False


LIVE = _lm_studio_available()


class TestGlossaryInjection(unittest.TestCase):
    """The cheng_zhu preset loads and reaches the VLM prompt (Phase-4.8 hook)."""

    def test_preset_is_listed(self):
        self.assertIn(GLOSSARY_NAME, list_glossaries())

    def test_loader_strips_comments_and_keeps_all_terms(self):
        terms = load_glossary_terms(GLOSSARY_NAME)
        # No comment text or the '#' marker should survive.
        self.assertNotIn("#", terms)
        self.assertNotIn("Scholar", terms)
        for term in GLOSSARY_TERMS:
            self.assertIn(term, terms)

    def test_terms_are_injected_into_prompt(self):
        terms = load_glossary_terms(GLOSSARY_NAME)
        prompt = build_prompt(
            custom_glossary=terms,
            reading_direction="Vertical RTL",
            document_structure="Main Text + Interline Commentary",
        )
        for term in GLOSSARY_TERMS:
            self.assertIn(term, prompt)
        # The classical-layout instructions must also be present.
        self.assertIn("interline commentary", prompt)
        self.assertIn("[brackets]", prompt)
        self.assertIn("right to left", prompt)


class TestCorrectionPairParsing(unittest.TestCase):
    """Phase 4.15: correction pairs parse correctly and inject a negative-prompt block."""

    def test_parser_separates_terms_and_pairs(self):
        std, pairs = parse_glossary("本義, 晝夜, 木義 -> 本義, 畫夜 -> 晝夜")
        self.assertIn("本義", std)
        self.assertIn("晝夜", std)
        self.assertIn(("木義", "本義"), pairs)
        self.assertIn(("畫夜", "晝夜"), pairs)

    def test_parser_whitespace_variants_equivalent(self):
        tight = parse_glossary("木義->本義")[1]
        spaced = parse_glossary("木義  ->  本義")[1]
        self.assertEqual(tight, [("木義", "本義")])
        self.assertEqual(tight, spaced)

    def test_parser_handles_newlines(self):
        std, pairs = parse_glossary("本義\n木義 -> 本義\n朱子曰")
        self.assertEqual(std, ["本義", "朱子曰"])
        self.assertEqual(pairs, [("木義", "本義")])

    def test_parser_strips_comments(self):
        std, pairs = parse_glossary("本義 # Title marker\n木義 -> 本義 # correction\n朱子曰")
        self.assertEqual(std, ["本義", "朱子曰"])
        self.assertEqual(pairs, [("木義", "本義")])

    def test_correction_pairs_present_in_glossary_file(self):
        _, pairs = parse_glossary(load_glossary_terms(GLOSSARY_NAME))
        self.assertIn(("木義", "本義"), pairs)
        self.assertIn(("畫夜", "晝夜"), pairs)

    def test_correction_pairs_not_injected_into_prompt(self):
        # Phase 4.16 removed the (ineffective) 4.15 negative-prompt block. Correction
        # pairs are now handled deterministically post-OCR, NOT in the prompt.
        prompt = build_prompt(custom_glossary=load_glossary_terms(GLOSSARY_NAME))
        self.assertNotIn("WARNING", prompt)
        self.assertNotIn("strict character substitutions", prompt)
        self.assertNotIn("Replace 木義 with 本義", prompt)
        # The standard-term block must still be present (no Phase-4.8 regression).
        self.assertIn("Prioritize matching these sequences visually", prompt)
        self.assertIn("朱子曰", prompt)


class TestApplyCorrections(unittest.TestCase):
    """Phase 4.16: deterministic post-OCR glyph correction."""

    PAIRS = [("木義", "本義"), ("畫夜", "晝夜")]

    def test_rewrites_misread_glyphs(self):
        raw = "本義一剛一柔各有定位。上繫曰剛柔者畫夜之象。木義曰。"
        fixed = apply_corrections(raw, self.PAIRS)
        self.assertIn("本義", fixed)
        self.assertIn("晝夜", fixed)
        self.assertNotIn("木義", fixed)
        self.assertNotIn("畫夜", fixed)

    def test_preserves_legitimate_single_glyph(self):
        # 剛柔之畫 legitimately uses 畫 (stroke) — only the bigram 畫夜 is rewritten.
        self.assertEqual(apply_corrections("剛柔之畫", self.PAIRS), "剛柔之畫")

    def test_idempotent_and_safe_on_empty(self):
        once = apply_corrections("木義畫夜", self.PAIRS)
        self.assertEqual(apply_corrections(once, self.PAIRS), once)
        self.assertEqual(apply_corrections("", self.PAIRS), "")
        self.assertEqual(apply_corrections("木義", []), "木義")

    def test_pipeline_pairs_drive_corrections(self):
        _, pairs = parse_glossary(load_glossary_terms(GLOSSARY_NAME))
        self.assertEqual(apply_corrections("木義…畫夜", pairs), "本義…晝夜")


class TestConsensusRecovery(unittest.TestCase):
    """3-way voting recovers the exact OCR errors found on page_008."""

    def test_vote_recovers_ben_and_zhou(self):
        # Same length, single-substitution variants so alignment is positional.
        correct = "本義一剛一柔各有定位晝夜之象"
        err_ben = "木義一剛一柔各有定位晝夜之象"  # 本 -> 木 (one pass wrong)
        err_zhou = "本義一剛一柔各有定位畫夜之象"  # 晝 -> 畫 (a different pass wrong)

        # Guard: all three distinct so vote_consensus does not short-circuit.
        self.assertEqual(len({correct, err_ben, err_zhou}), 3)

        result = vote_consensus(correct, err_ben, err_zhou)
        self.assertIn("本義", result)
        self.assertNotIn("木義", result)
        self.assertIn("晝夜", result)
        self.assertNotIn("畫夜", result)


class TestOverlapStitching(unittest.TestCase):
    """Overlap stitching reconstructs a split transcript seam-clean (double-column proxy)."""

    def test_merge_reconstructs_split(self):
        text = GROUND_TRUTH
        n = len(text)
        mid = n // 2
        overlap = 80
        top = text[: mid + overlap]
        bottom = text[mid - overlap:]

        merged = merge_overlapping_texts(top, bottom)

        # Content preserved and corrected characters survive the seam.
        self.assertIn("本義", merged)
        self.assertNotIn("木義", merged)
        self.assertIn("晝夜", merged)
        # A found seam yields ~n chars; a failed seam would concatenate to ~n+2*overlap.
        self.assertLessEqual(len(merged), n + 20)


class TestCorrectionPipelineOffline(unittest.IsolatedAsyncioTestCase):
    """
    Deterministic end-to-end proof of the Phase-4.16 correction net through the REAL
    pipeline, with NO VLM: seed a cached page containing the raw misreads, then run
    process_pdf_to_markdown. Resumption reads the cache, apply_corrections rewrites the
    glyphs, and the merge/TOC/write path produces a corrected _FULL.md.
    """

    async def test_resumption_applies_corrections_end_to_end(self):
        import shutil
        from backend.config import get_output_dir
        from backend.ocr_engine import process_pdf_to_markdown

        self.assertTrue(PDF_PATH.exists(), f"missing test PDF: {PDF_PATH}")
        outd = get_output_dir()
        cache = outd / ".page_008_pages"
        out_md = outd / "page_008_FULL.md"

        # Seed a cached page containing the exact misreads the VLM produces.
        if cache.exists():
            shutil.rmtree(cache, ignore_errors=True)
        cache.mkdir(parents=True, exist_ok=True)
        (cache / "page_001.md").write_text(
            "剛柔者立本者也。木義一剛一柔各有定位。上繫曰剛柔者畫夜之象。吉凶者貞勝者也。",
            encoding="utf-8",
        )

        settings = load_settings()
        settings["custom_glossary"] = load_glossary_terms(GLOSSARY_NAME)
        settings["reading_direction"] = "Default"
        settings["page_range"] = ""
        try:
            result = await process_pdf_to_markdown(str(PDF_PATH), settings, None)
            text = (outd / result["output_filename"]).read_text(encoding="utf-8")
        finally:
            shutil.rmtree(cache, ignore_errors=True)
            if out_md.exists():
                out_md.unlink()

        self.assertIn("本義", text)      # 木義 -> 本義 applied
        self.assertNotIn("木義", text)
        self.assertIn("晝夜", text)      # 畫夜 -> 晝夜 applied
        self.assertNotIn("畫夜", text)


@unittest.skipUnless(LIVE, "LM Studio server not reachable — live benchmark skipped")
class TestPage008LiveOCR(unittest.IsolatedAsyncioTestCase):
    """End-to-end benchmark against the real VLM (only when LM Studio is up)."""

    _cached_text = None  # memo so both tests share one pipeline run

    async def _run_pipeline(self) -> str:
        cls = type(self)
        if cls._cached_text is not None:
            return cls._cached_text

        import shutil
        from backend.config import get_output_dir
        from backend.ocr_engine import process_pdf_to_markdown

        self.assertTrue(PDF_PATH.exists(), f"missing test PDF: {PDF_PATH}")
        outd = get_output_dir()

        # Clear any stale page_008 cache so this is a TRUE fresh-OCR benchmark of the
        # deterministic correction net, not a resumed pre-4.16 cache (Phase 4.16 D-4).
        cache = outd / ".page_008_pages"
        if cache.exists():
            shutil.rmtree(cache, ignore_errors=True)
        for f in ("page_008_FULL.md", "page_008_FULL.html", "page_008_FULL.docx"):
            p = outd / f
            if p.exists():
                p.unlink()

        settings = load_settings()
        settings["reading_direction"] = "Vertical RTL"
        settings["document_structure"] = "Main Text + Interline Commentary"
        settings["custom_glossary"] = load_glossary_terms(GLOSSARY_NAME)
        settings["consensus_mode"] = False
        settings["page_range"] = ""

        result = await process_pdf_to_markdown(str(PDF_PATH), settings, None)
        text = (outd / result["output_filename"]).read_text(encoding="utf-8")

        # Distinguish a code regression from a flaky/unhealthy server: if LM Studio
        # could not actually OCR the page (400/500 image or tokenize errors), skip
        # rather than false-fail. The deterministic net is proven offline in
        # TestCorrectionPipelineOffline regardless of live server health.
        if "FAILED OR EMPTY" in text or result.get("pages_completed", 0) == 0:
            self.skipTest("LM Studio could not OCR the page (server/model error); live benchmark skipped.")

        cls._cached_text = text
        return cls._cached_text

    async def test_live_structural_fidelity(self):
        """Hard benchmark: RTL + double-column reading order must stay >= 0.90 similar."""
        text = await self._run_pipeline()
        ratio = difflib.SequenceMatcher(None, GROUND_TRUTH, text).ratio()
        self.assertGreaterEqual(
            ratio, 0.90,
            f"OCR structural similarity {ratio:.3f} fell below the 0.90 benchmark",
        )

    async def test_live_glossary_glyph_corrections(self):
        """
        The glyph misreads are now fixed by the Phase-4.16 deterministic post-OCR
        correction net (the VLM still emits 木義/畫夜 — empirically proven in 4.14/4.15
        that no prompt can stop it — but apply_corrections rewrites them before the
        text is cached/merged/exported). This test asserts the net works end-to-end.
        """
        text = await self._run_pipeline()
        self.assertIn("本義", text)
        self.assertNotIn("木義", text)
        self.assertIn("晝夜", text)
        self.assertNotIn("畫夜", text)


if __name__ == "__main__":
    unittest.main()
