"""
OCR Studio — Bugs Hunt regression tests (2026-07-06, wargame 01-bugs).

Fixable findings assert the CORRECT post-fix behaviour (they fail before the
fix, pass after). Report-only findings are marked @expectedFailure and carry
the finding ID, so the suite stays green while documenting the defect; if a
later pass fixes one it flips to an unexpected success and flags the baseline.

Findings covered:
  H-3  FIX        WS broadcast must survive a client set mutation mid-broadcast
  H-2  FIX        vote_consensus must not truncate/drop text on unequal passes
  H-5  FIX        consensus pages return token_logprobs=None (UI renders voted text)
  H-4  FIX        auto-shutdown must not kill the process while a job is busy
  H-7  FIX        _run_job must not run a job already marked CANCELLED
"""

import asyncio
import unittest
from unittest import mock

from backend.job_manager import JobManager
from backend.models import ProgressMessage, JobResponse, JobStatus
from backend.ocr_engine import vote_consensus, PageResponse, PageResult, run_consensus_ocr


# ---------------------------------------------------------------------------
# H-3 (FIX) — _broadcast must not raise when the client set mutates mid-await
# ---------------------------------------------------------------------------
class TestBroadcastRace(unittest.IsolatedAsyncioTestCase):
    async def test_broadcast_survives_client_registration_midway(self):
        jm = JobManager()

        class FakeWS:
            def __init__(self, on_send=None):
                self.on_send = on_send
                self.received = 0

            async def send_text(self, payload):
                self.received += 1
                if self.on_send is not None:
                    # A new client connects during the await window (real scenario:
                    # a browser tab opens mid-broadcast). This mutates the set the
                    # buggy code is iterating.
                    jm._websocket_clients.add(self.on_send)
                    self.on_send = None
                await asyncio.sleep(0)

        intruder = FakeWS()
        a = FakeWS(on_send=intruder)
        b = FakeWS()
        jm._websocket_clients.update({a, b})

        # Must not raise RuntimeError('Set changed size during iteration').
        await jm._broadcast(ProgressMessage(job_id="x", event="page_complete"))

        # Both originally-registered clients still received the message.
        self.assertEqual(a.received, 1)
        self.assertEqual(b.received, 1)


# ---------------------------------------------------------------------------
# H-2 (FIX) — consensus voting must not silently drop text on unequal passes
# ---------------------------------------------------------------------------
class TestConsensusDataLoss(unittest.TestCase):
    def test_consensus_preserves_tail_on_insertion(self):
        s1, s2, s3 = "ABCD", "ABXD", "AB12CD"
        out = vote_consensus(s1, s2, s3)
        # Before the fix this collapsed to 'ABXD' (len 4): zip() truncated the vote
        # to the shortest aligned row, dropping the 'CD' tail. After the fix s2 is
        # aligned to the s3-expanded reference and all rows are padded to max length,
        # so the tail survives and nothing is truncated.
        self.assertTrue(out.endswith("CD"), f"consensus truncated the tail: {out!r}")
        self.assertGreaterEqual(len(out), 5, f"consensus dropped chars: {out!r}")

    def test_consensus_unanimous_and_majority_unaffected(self):
        # Guard: the fix must not regress the ordinary majority/early-return paths.
        self.assertEqual(vote_consensus("Hello World", "Hella World", "Hello Warld"),
                         "Hello World")
        self.assertEqual(vote_consensus("ABCDEF", "AB-DEF", "ABCDEF"), "ABCDEF")


# ---------------------------------------------------------------------------
# H-5 (FIX) — consensus pages must return token_logprobs=None so the editor
#             renders the voted natural_text instead of the base pass's stream
# ---------------------------------------------------------------------------
class TestConsensusTokenStreamMismatch(unittest.IsolatedAsyncioTestCase):
    async def test_consensus_returns_no_token_stream(self):
        def mk(text):
            resp = PageResponse(
                primary_language="zh", is_rotation_valid=True, rotation_correction=0,
                is_table=False, is_diagram=False, natural_text=text,
            )
            toks = [{"token": ch, "logprob": -0.01, "confidence": 99.0, "top_logprobs": []}
                    for ch in text]
            return PageResult(page_num=1, response=resp, success=True,
                              confidence_score=95.0, token_logprobs=toks)

        # Three passes: two read 本 correctly, the 1288px base (r2) misread 木.
        results = [mk("讀本義書"), mk("讀木義書"), mk("讀本義書")]
        with mock.patch("backend.ocr_engine.process_single_page",
                        side_effect=results):
            merged = await run_consensus_ocr(
                http_client=None, pdf_path="x", page_num=1, server_url="u", model="m",
                max_tokens=100, max_retries=0, custom_glossary="", strict_mode=False,
                reading_direction="Default", previous_page_context="",
                document_structure="Standard", binarize=False, high_contrast=False,
                despeckle=False,
            )
        # The voted text is stored...
        self.assertEqual(merged.response.natural_text, "讀本義書")
        # ...and no token stream is returned, so the frontend cannot revert the vote
        # to r2's raw 木 glyph (it renders p.content = the voted text instead).
        self.assertIsNone(merged.token_logprobs,
                          "consensus must not ship the base pass's token stream")


# ---------------------------------------------------------------------------
# H-4 (FIX) — auto-shutdown must not kill an in-flight job
# ---------------------------------------------------------------------------
class TestAutoShutdownBusyGuard(unittest.IsolatedAsyncioTestCase):
    async def test_no_shutdown_while_busy(self):
        jm = JobManager()
        jm._active_job_id = "running-job"     # a job is in flight
        jm._websocket_clients.clear()         # last client just disconnected

        def _swallow(coro):
            # Close the re-arm coroutine so it doesn't recurse (sleep is mocked to a
            # no-op) or warn "never awaited"; we only assert that it was scheduled.
            coro.close()
            return mock.MagicMock()

        with mock.patch("backend.job_manager.os.kill") as killer, \
             mock.patch("backend.job_manager.asyncio.create_task",
                        side_effect=_swallow) as rearm, \
             mock.patch("backend.job_manager.asyncio.sleep",
                        new=mock.AsyncMock(return_value=None)):
            await jm._auto_shutdown_check()
        killer.assert_not_called()
        # G-2: while busy it must DEFER (re-arm) rather than die silently.
        rearm.assert_called_once()


# ---------------------------------------------------------------------------
# H-7 (FIX) — a job cancelled in the queue gap must not run
# ---------------------------------------------------------------------------
class TestRunJobRespectsCancel(unittest.IsolatedAsyncioTestCase):
    async def test_cancelled_job_is_not_processed(self):
        jm = JobManager()
        job = JobResponse(job_id="j1", status=JobStatus.CANCELLED, pdf_filename="a.pdf")
        jm._jobs["j1"] = job
        ran = {"flag": False}

        async def fake_pipeline(*args, **kwargs):
            ran["flag"] = True
            return {"total_pages": 1, "output_filename": "a_FULL.md",
                    "pages_completed": 1, "duration_seconds": 0.1}

        with mock.patch("backend.job_manager.process_pdf_to_markdown",
                        side_effect=fake_pipeline):
            await jm._run_job("j1", "a.pdf", {})
        self.assertFalse(ran["flag"], "cancelled job was processed anyway")
        self.assertEqual(job.status, JobStatus.CANCELLED)


# ---------------------------------------------------------------------------
# G-1 (FIX) — .md sideload registration must honor the bounded job history
# ---------------------------------------------------------------------------
class TestSideloadJobHistoryBounded(unittest.IsolatedAsyncioTestCase):
    async def test_register_completed_job_trims_history(self):
        from backend.job_manager import MAX_JOB_HISTORY
        jm = JobManager()
        for i in range(MAX_JOB_HISTORY + 5):
            await jm.register_completed_job(
                JobResponse(job_id=f"md{i}", status=JobStatus.COMPLETED, pdf_filename=f"{i}.md")
            )
        self.assertLessEqual(len(jm._jobs), MAX_JOB_HISTORY, "sideload history grew past the cap")
        self.assertNotIn("md0", jm._jobs)                       # oldest evicted (FIFO)
        self.assertIn(f"md{MAX_JOB_HISTORY + 4}", jm._jobs)     # newest retained


if __name__ == "__main__":
    unittest.main()
