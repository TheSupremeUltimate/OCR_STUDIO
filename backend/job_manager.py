"""
OCR Studio — Job Manager

Manages OCR job lifecycle: creation, execution, progress tracking,
and WebSocket broadcasting to connected clients.
"""

import asyncio
import datetime
import logging
import os
import signal
import time
import uuid
from collections import OrderedDict
from typing import Optional

from fastapi import WebSocket

from backend.models import JobResponse, JobStatus, ProgressMessage
from backend.ocr_engine import process_pdf_to_markdown

logger = logging.getLogger("ocr_studio.jobs")

MAX_JOB_HISTORY = 20


class JobManager:
    """Manages OCR jobs and broadcasts progress to WebSocket clients."""

    def __init__(self):
        self._jobs: OrderedDict[str, JobResponse] = OrderedDict()
        self._active_task: Optional[asyncio.Task] = None
        self._active_job_id: Optional[str] = None
        self._global_page_times: list[float] = []
        self._websocket_clients: set[WebSocket] = set()
        self._lock = asyncio.Lock()

    async def start(self):
        """Start the background queue worker and startup safety check."""
        self._queue = asyncio.Queue()
        self._worker_task = asyncio.create_task(self._worker())
        self._startup_check_task = asyncio.create_task(self._startup_shutdown_check())

    async def _worker(self):
        """Background worker that pulls jobs from the queue and processes them sequentially."""
        while True:
            try:
                job_id, pdf_path, settings = await self._queue.get()
                self._active_job_id = job_id
                
                job = self._jobs.get(job_id)
                if job and job.status == JobStatus.CANCELLED:
                    self._queue.task_done()
                    self._active_job_id = None
                    continue

                try:
                    self._active_task = asyncio.create_task(self._run_job(job_id, pdf_path, settings))
                    await self._active_task
                except asyncio.CancelledError:
                    pass
                except Exception as e:
                    logger.exception("Error processing job %s in queue worker: %s", job_id, e)
                finally:
                    self._active_task = None
                    self._active_job_id = None
                    self._queue.task_done()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error("Unexpected error in queue worker: %s", e)
                await asyncio.sleep(1)

    # ------------------------------------------------------------------
    # WebSocket client management
    # ------------------------------------------------------------------

    async def register_client(self, ws: WebSocket):
        """Register a WebSocket client for progress updates."""
        self._websocket_clients.add(ws)
        logger.info("WebSocket client connected (%d total)", len(self._websocket_clients))

    async def unregister_client(self, ws: WebSocket):
        """Unregister a WebSocket client."""
        self._websocket_clients.discard(ws)
        logger.info("WebSocket client disconnected (%d remaining)", len(self._websocket_clients))
        if len(self._websocket_clients) == 0:
            asyncio.create_task(self._auto_shutdown_check())

    async def _startup_shutdown_check(self):
        """Shut down if no browser client connects within 20 seconds of server startup."""
        await asyncio.sleep(20)
        if len(self._websocket_clients) == 0:
            logger.info("No UI clients connected during startup grace period. Shutting down OCR Studio...")
            os.kill(os.getpid(), signal.SIGINT)

    async def _auto_shutdown_check(self):
        """Shut down if all UI clients disconnect and remain disconnected for 5 seconds."""
        await asyncio.sleep(5)
        if len(self._websocket_clients) == 0:
            logger.info("No active UI clients remaining. Shutting down OCR Studio...")
            os.kill(os.getpid(), signal.SIGINT)

    async def _broadcast(self, message: ProgressMessage):
        """Send a progress message to all connected WebSocket clients."""
        if not self._websocket_clients:
            return

        payload = message.model_dump_json()
        dead_clients = set()

        for ws in self._websocket_clients:
            try:
                await ws.send_text(payload)
            except Exception:
                dead_clients.add(ws)

        # Clean up disconnected clients
        for ws in dead_clients:
            self._websocket_clients.discard(ws)

    # ------------------------------------------------------------------
    # Job lifecycle
    # ------------------------------------------------------------------

    async def create_job(self, pdf_path: str, pdf_filename: str, settings: dict) -> str:
        """Create a new OCR job and add it to the queue."""
        async with self._lock:
            job_id = str(uuid.uuid4())[:8]
            now = datetime.datetime.now().isoformat(timespec="seconds")

            # Count pages
            from pypdf import PdfReader
            reader = PdfReader(pdf_path)
            total_pages = len(reader.pages)

            job = JobResponse(
                job_id=job_id,
                status=JobStatus.QUEUED,
                pdf_filename=pdf_filename,
                progress_percent=0.0,
                pages_completed=0,
                pages_total=total_pages,
                pages_failed=0,
                created_at=now,
            )
            self._jobs[job_id] = job

            # Trim history
            while len(self._jobs) > MAX_JOB_HISTORY:
                self._jobs.popitem(last=False)

            # Enqueue the job
            if hasattr(self, "_queue"):
                await self._queue.put((job_id, pdf_path, settings))
            else:
                logger.warning("Queue not initialized, starting job directly (fallback)")
                self._active_task = asyncio.create_task(self._run_job(job_id, pdf_path, settings))

            logger.info("Created job %s for %s (%d pages), added to queue", job_id, pdf_filename, total_pages)
            return job_id

    async def _run_job(self, job_id: str, pdf_path: str, settings: dict):
        """Execute an OCR job in the background."""
        job = self._jobs.get(job_id)
        if not job:
            return

        # Update status to processing
        job.status = JobStatus.PROCESSING

        await self._broadcast(ProgressMessage(
            job_id=job_id,
            event="job_start",
            pages_total=job.pages_total,
            message=f"Starting OCR: {job.pdf_filename} ({job.pages_total} pages)",
        ))

        try:
            # Define the progress callback
            page_starts = {}

            async def on_progress(page_num: int, total_pages: int, event: str, message: str, eta: Optional[float] = None, confidence: Optional[float] = None, token_logprobs: Optional[list] = None):
                done = job.pages_completed + job.pages_failed
                
                if event == "page_start":
                    page_starts[page_num] = time.time()
                elif event == "page_complete":
                    job.pages_completed += 1
                    done = job.pages_completed + job.pages_failed
                    if confidence is not None:
                        job.page_confidence[str(page_num)] = confidence
                    if token_logprobs is not None:
                        job.page_token_logprobs[str(page_num)] = token_logprobs
                    if page_num in page_starts:
                        duration = time.time() - page_starts.pop(page_num)
                        self._global_page_times.append(duration)
                        if len(self._global_page_times) > 50:
                            self._global_page_times.pop(0)
                elif event == "page_failed":
                    job.pages_failed += 1
                    done = job.pages_completed + job.pages_failed
                    if page_num in page_starts:
                        duration = time.time() - page_starts.pop(page_num)
                        self._global_page_times.append(duration)
                        if len(self._global_page_times) > 50:
                            self._global_page_times.pop(0)

                job.pages_total = total_pages
                job.progress_percent = round((done / total_pages) * 100, 1) if total_pages > 0 else 0

                # Compute smart fallback ETA if local ETA is None and we aren't done yet
                calculated_eta = eta
                if calculated_eta is None and done < total_pages:
                    if self._global_page_times:
                        avg_time = sum(self._global_page_times) / len(self._global_page_times)
                    else:
                        avg_time = 25.0  # Fallback default: 25 seconds per page
                    
                    workers = settings.get("workers", 2)
                    remaining = total_pages - done
                    calculated_eta = avg_time * remaining / max(workers, 1)

                await self._broadcast(ProgressMessage(
                    job_id=job_id,
                    event=event,
                    page_num=page_num,
                    pages_total=total_pages,
                    pages_completed=job.pages_completed,
                    pages_failed=job.pages_failed,
                    progress_percent=job.progress_percent,
                    message=message,
                    eta_seconds=calculated_eta,
                    confidence=confidence,
                    token_logprobs=token_logprobs,
                ))

            # Run the OCR engine
            result = await process_pdf_to_markdown(pdf_path, settings, on_progress)

            # Update job with results
            job.status = JobStatus.COMPLETED
            job.pages_total = result["total_pages"]
            job.output_filename = result["output_filename"]
            job.progress_percent = 100.0
            job.completed_at = datetime.datetime.now().isoformat(timespec="seconds")
            job.total_runtime = result.get("duration_seconds")
            job.average_confidence = result.get("average_confidence")
            job.total_retries = result.get("total_retries", 0)

            await self._broadcast(ProgressMessage(
                job_id=job_id,
                event="job_complete",
                pages_total=job.pages_total,
                pages_completed=job.pages_completed,
                pages_failed=job.pages_failed,
                progress_percent=100.0,
                message=f"Complete: {result['pages_completed']}/{result['total_pages']} pages ({result['duration_seconds']}s)",
                total_runtime=job.total_runtime,
                average_confidence=job.average_confidence,
                total_retries=job.total_retries,
            ))

            logger.info("Job %s completed: %s", job_id, result["output_filename"])

        except asyncio.CancelledError:
            job.status = JobStatus.CANCELLED
            job.completed_at = datetime.datetime.now().isoformat(timespec="seconds")
            logger.info("Job %s cancelled", job_id)
            raise

        except Exception as e:
            job.status = JobStatus.FAILED
            job.error_message = str(e)
            job.completed_at = datetime.datetime.now().isoformat(timespec="seconds")

            await self._broadcast(ProgressMessage(
                job_id=job_id,
                event="job_failed",
                progress_percent=job.progress_percent,
                message=f"Job failed: {e}",
            ))

            logger.exception("Job %s failed: %s", job_id, e)

        finally:
            self._active_job_id = None

    # ------------------------------------------------------------------
    # Job queries
    # ------------------------------------------------------------------

    def get_job(self, job_id: str) -> Optional[JobResponse]:
        """Get the status of a specific job."""
        return self._jobs.get(job_id)

    def get_all_jobs(self) -> list[JobResponse]:
        """Get all jobs (most recent first)."""
        return list(reversed(self._jobs.values()))

    def is_busy(self) -> bool:
        """Check if a job is currently running."""
        return self._active_job_id is not None

    def cancel_job(self, job_id: str) -> bool:
        """Cancel a running job by job_id."""
        if self._active_job_id == job_id and self._active_task and not self._active_task.done():
            self._active_task.cancel()
            return True

        job = self._jobs.get(job_id)
        if job and job.status in (JobStatus.QUEUED, JobStatus.PROCESSING):
            job.status = JobStatus.CANCELLED
            job.completed_at = datetime.datetime.now().isoformat(timespec="seconds")
            return True

        return False

    def clear_jobs(self):
        """Clear all job history."""
        self._jobs.clear()
