import json
import re
import unittest
from unittest.mock import MagicMock, patch, AsyncMock
from fastapi.testclient import TestClient

from backend.main import app, _strip_reasoning
from backend.ocr_engine import (
    align_strings,
    vote_consensus,
    merge_overlapping_texts,
    PageResult,
    PageResponse,
)


def _parse_sse(response):
    """Parse a buffered text/event-stream body into a list of JSON event dicts.

    The translate endpoint now streams Server-Sent Events; TestClient buffers the
    whole response, so we split on the SSE record separator ("\\n\\n") and decode
    each ``data:`` line's JSON payload.
    """
    events = []
    for block in response.text.split("\n\n"):
        for line in block.split("\n"):
            if line.startswith("data:"):
                payload = line[len("data:"):].strip()
                if payload:
                    events.append(json.loads(payload))
    return events


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
        self.assertIn("text/event-stream", response.headers["content-type"])
        events = _parse_sse(response)
        # At least one per-chunk progress event, then a single terminal completed event.
        self.assertTrue(any(e.get("status") == "processing" for e in events))
        completed = [e for e in events if e.get("status") == "completed"]
        self.assertEqual(len(completed), 1)
        self.assertEqual(completed[0]["translated_text"], "English translation result")

    @patch("backend.main.load_settings")
    @patch("httpx.AsyncClient.post")
    def test_translate_first_progress_before_work(self, mock_post, mock_settings):
        # Telemetry fix: a "processing" event for chunk 1 must be emitted at the
        # START of the loop (progress_pct 0.0), before the slow LM Studio call,
        # so the button updates immediately instead of only after chunk 1 finishes.
        mock_settings.return_value = {
            "server_url": "http://localhost:1234/v1",
            "model": "allenai_olmocr-2-7b-1025",
        }
        mock_res = MagicMock()
        mock_res.status_code = 200
        mock_res.json.return_value = {
            "choices": [{"message": {"content": "English translation result"}}]
        }
        mock_post.return_value = mock_res

        response = self.client.post(
            "/api/jobs/translate",
            json={"content": "Classical Chinese text content"}
        )
        self.assertEqual(response.status_code, 200)
        events = _parse_sse(response)
        # The very first event is a chunk-1 progress ping emitted before any work.
        self.assertEqual(events[0]["status"], "processing")
        self.assertEqual(events[0]["current_chunk"], 1)
        self.assertEqual(events[0]["progress_pct"], 0.0)
        # A processing event must precede the terminal completed event.
        statuses = [e["status"] for e in events]
        self.assertLess(statuses.index("processing"), statuses.index("completed"))

    @patch("backend.main.load_settings")
    @patch("httpx.AsyncClient.post")
    def test_translate_preserves_exact_page_markers(self, mock_post, mock_settings):
        # Regression for the runaway repetition loop: a model that leaks/loops
        # <!-- PAGE NNN --> markers into its output must NOT corrupt the page
        # structure. Markers are stripped from model output and reassembled in
        # code, so only the original input markers (001, 002, 003) appear — never
        # phantom pages 099/100.
        mock_settings.return_value = {"server_url": "http://localhost:1234/v1", "model": "m"}
        mock_res = MagicMock()
        mock_res.status_code = 200
        mock_res.json.return_value = {
            "choices": [{"message": {"content": "<!-- PAGE 099 -->\nEnglish\n<!-- PAGE 100 -->\ntail"}}]
        }
        mock_post.return_value = mock_res

        src = "<!-- PAGE 001 -->\n\n甲\n\n<!-- PAGE 002 -->\n\n乙\n\n<!-- PAGE 003 -->\n\n丙"
        response = self.client.post("/api/jobs/translate", json={"content": src})
        self.assertEqual(response.status_code, 200)
        completed = [e for e in _parse_sse(response) if e.get("status") == "completed"]
        self.assertEqual(len(completed), 1)
        markers = re.findall(r"<!-- PAGE (\d+) -->", completed[0]["translated_text"])
        self.assertEqual(markers, ["001", "002", "003"])
        # And no leaked marker survived inside the page bodies.
        self.assertNotIn("099", completed[0]["translated_text"])
        self.assertNotIn("100", completed[0]["translated_text"])

    @patch("backend.main.load_settings")
    @patch("httpx.AsyncClient.post")
    def test_translate_memoizes_identical_pages(self, mock_post, mock_settings):
        # Non-consecutive identical page contents are translated once and reused
        # (memoization), cutting redundant LM Studio calls. Page 3 is a duplicate of
        # page 1 but NOT consecutive, so it is filled from cache (not blanked).
        mock_settings.return_value = {"server_url": "http://localhost:1234/v1", "model": "m"}
        mock_res = MagicMock()
        mock_res.status_code = 200
        mock_res.json.return_value = {"choices": [{"message": {"content": "EN"}}]}
        mock_post.return_value = mock_res

        # Pages 1 & 3 share content (序); page 2 differs (異) — 1 and 3 not adjacent.
        src = "<!-- PAGE 001 -->\n\n序\n\n<!-- PAGE 002 -->\n\n異\n\n<!-- PAGE 003 -->\n\n序"
        response = self.client.post("/api/jobs/translate", json={"content": src})
        self.assertEqual(response.status_code, 200)
        completed = [e for e in _parse_sse(response) if e.get("status") == "completed"]
        self.assertEqual(len(completed), 1)
        text = completed[0]["translated_text"]
        self.assertEqual(re.findall(r"<!-- PAGE (\d+) -->", text), ["001", "002", "003"])
        # Two unique contents => two model calls (page 3 served from cache).
        self.assertEqual(mock_post.call_count, 2)
        # Page 3 (non-consecutive duplicate) is filled from cache, NOT blanked.
        bodies = re.split(r"<!-- PAGE \d+ -->", text)
        self.assertIn("EN", bodies[3])

    @patch("backend.main.load_settings")
    @patch("httpx.AsyncClient.post")
    def test_translate_is_faithful_to_duplicate_pages(self, mock_post, mock_settings):
        # Faithful translation: consecutive identical source pages are each
        # reproduced in the output (nothing is blanked). Memoization still avoids
        # re-calling the model, but every page keeps its translated body.
        mock_settings.return_value = {"server_url": "http://localhost:1234/v1", "model": "m"}
        mock_res = MagicMock()
        mock_res.status_code = 200
        mock_res.json.return_value = {"choices": [{"message": {"content": "EN"}}]}
        mock_post.return_value = mock_res

        # Pages 1-3 identical (序); page 4 differs (異).
        src = ("<!-- PAGE 001 -->\n\n序\n\n<!-- PAGE 002 -->\n\n序\n\n"
               "<!-- PAGE 003 -->\n\n序\n\n<!-- PAGE 004 -->\n\n異")
        response = self.client.post("/api/jobs/translate", json={"content": src})
        self.assertEqual(response.status_code, 200)
        completed = [e for e in _parse_sse(response) if e.get("status") == "completed"]
        self.assertEqual(len(completed), 1)
        text = completed[0]["translated_text"]
        self.assertEqual(re.findall(r"<!-- PAGE (\d+) -->", text), ["001", "002", "003", "004"])
        bodies = re.split(r"<!-- PAGE \d+ -->", text)
        # Every page body is populated — duplicates are NOT blanked.
        for i in range(1, 5):
            self.assertIn("EN", bodies[i])
        # Two distinct contents => two model calls (identical pages served from cache).
        self.assertEqual(mock_post.call_count, 2)

    def test_strip_reasoning_helper(self):
        # Complete <think> block removed (multiline)
        self.assertEqual(
            _strip_reasoning("<think>line1\nline2</think>Final answer"),
            "Final answer",
        )
        # Unclosed / truncated <think> -> drop everything from the tag onward
        self.assertEqual(_strip_reasoning("<think>reasoning with no close"), "")
        # No tags -> unchanged (trimmed)
        self.assertEqual(_strip_reasoning("  plain text  "), "plain text")
        # Empty input
        self.assertEqual(_strip_reasoning(""), "")
        # Case-insensitive tag
        self.assertEqual(_strip_reasoning("<THINK>x</THINK>ans"), "ans")

    @patch("backend.main.load_settings")
    @patch("httpx.AsyncClient.post")
    def test_translate_strips_think_block(self, mock_post, mock_settings):
        # A reasoning model that leaks <think> into content should have it stripped.
        mock_settings.return_value = {
            "server_url": "http://localhost:1234/v1",
            "model": "m",
            "translation_model": "qwen3.5-35b-a3b",
        }
        mock_res = MagicMock()
        mock_res.status_code = 200
        mock_res.json.return_value = {
            "choices": [{"message": {
                "content": "<think>Analyze the request...\nStep 1...</think>The English translation."
            }}]
        }
        mock_post.return_value = mock_res

        response = self.client.post("/api/jobs/translate", json={"content": "..."})
        self.assertEqual(response.status_code, 200)
        completed = [e for e in _parse_sse(response) if e.get("status") == "completed"]
        self.assertEqual(len(completed), 1)
        self.assertEqual(completed[0]["translated_text"], "The English translation.")

    @patch("backend.main.load_settings")
    @patch("httpx.AsyncClient.post")
    def test_translate_empty_content_streams_error(self, mock_post, mock_settings):
        # content=null with the answer stuck in reasoning_content -> fail loud, not blank.
        # SSE cannot change the HTTP status once the stream starts (200 is already
        # sent), so the failure is surfaced as an in-band {"status":"error"} event.
        mock_settings.return_value = {
            "server_url": "http://localhost:1234/v1",
            "model": "m",
            "translation_model": "qwen3.5-35b-a3b",
        }
        mock_res = MagicMock()
        mock_res.status_code = 200
        mock_res.json.return_value = {
            "choices": [{"message": {"content": None, "reasoning_content": "lots of thinking..."}}]
        }
        mock_post.return_value = mock_res

        response = self.client.post("/api/jobs/translate", json={"content": "..."})
        self.assertEqual(response.status_code, 200)
        events = _parse_sse(response)
        errors = [e for e in events if e.get("status") == "error"]
        self.assertEqual(len(errors), 1)
        self.assertIn("no content", errors[0]["detail"])
        # No completed event must be emitted on failure.
        self.assertFalse(any(e.get("status") == "completed" for e in events))

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

