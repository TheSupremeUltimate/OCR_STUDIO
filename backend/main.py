"""
OCR Studio — FastAPI Application

Main entry point for the OCR Studio backend. Serves the REST API,
WebSocket endpoint, and static frontend files.
"""

import logging
import os
import re
import shutil
import time
import sys
import subprocess
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional, Union

import httpx
import orjson
from fastapi import FastAPI, File, Request, UploadFile, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pypdf import PdfReader

from backend.config import (
    LOGS_DIR,
    PROJECT_ROOT,
    ensure_directories,
    load_settings,
    save_settings,
    get_output_dir,
    get_upload_dir,
    resolve_within_base,
    get_glossaries_dir,
    list_glossaries,
    load_glossary_terms,
)
from backend.job_manager import JobManager
from backend.ocr_engine import process_cropped_zone
from backend.models import (
    JobCreateRequest,
    JobResponse,
    JobStatus,
    SaveFileRequest,
    ServerHealthResponse,
    SettingsResponse,
    SettingsUpdateRequest,
    UploadResponse,
    ZoneReprocessRequest,
    TranslateRequest,
)

# ---------------------------------------------------------------------------
# Logging setup
# ---------------------------------------------------------------------------

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler(LOGS_DIR / "ocr_studio.log", mode="a", encoding="utf-8"),
    ],
)
logger = logging.getLogger("ocr_studio.app")

# ---------------------------------------------------------------------------
# Application setup
# ---------------------------------------------------------------------------

app = FastAPI(
    title="OCR Studio",
    description="Local web GUI for the OlmOCR pipeline",
    version="0.1.0",
)

# Local-only trust posture: restrict cross-origin access to the app's own
# origin. The frontend is served same-origin, so this blocks drive-by requests
# from arbitrary websites without affecting normal operation.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:8080", "http://127.0.0.1:8080"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# Global instances
job_manager = JobManager()


# ---------------------------------------------------------------------------
# Startup / Shutdown
# ---------------------------------------------------------------------------

@app.on_event("startup")
async def startup():
    ensure_directories()
    await job_manager.start()
    logger.info("OCR Studio backend started")
    logger.info("Frontend: http://localhost:8080")
    logger.info("Output dir: %s", get_output_dir())


# ---------------------------------------------------------------------------
# API Routes — Health
# ---------------------------------------------------------------------------

@app.get("/api/health", response_model=ServerHealthResponse)
async def check_health():
    """Check connectivity to the LM Studio server."""
    settings = load_settings()
    server_url = settings.get("server_url", "")

    try:
        start = time.time()
        async with httpx.AsyncClient() as client:
            response = await client.get(
                f"{server_url.rstrip('/')}/models",
                timeout=10.0,
            )
        elapsed_ms = (time.time() - start) * 1000

        if response.status_code == 200:
            data = response.json()
            models = data.get("data", [])
            model_id = models[0]["id"] if models else None
            return ServerHealthResponse(
                reachable=True,
                model_loaded=model_id,
                response_time_ms=round(elapsed_ms, 1),
            )
        else:
            return ServerHealthResponse(
                reachable=False,
                error=f"Server returned {response.status_code}",
            )
    except Exception as e:
        return ServerHealthResponse(
            reachable=False,
            error=str(e),
        )


# ---------------------------------------------------------------------------
# API Routes — Settings
# ---------------------------------------------------------------------------

@app.get("/api/settings", response_model=SettingsResponse)
async def get_settings():
    """Get current application settings."""
    settings = load_settings()
    return SettingsResponse(**settings)


@app.put("/api/settings", response_model=SettingsResponse)
async def update_settings(req: SettingsUpdateRequest):
    """Update application settings."""
    settings = load_settings()

    # Apply only non-None updates
    update_data = req.model_dump(exclude_none=True)
    settings.update(update_data)

    save_settings(settings)
    return SettingsResponse(**settings)


@app.get("/api/models")
async def get_models():
    """Fetch available models from the connected LM Studio server."""
    settings = load_settings()
    server_url = settings.get("server_url", "")
    if not server_url:
        return []
    try:
        async with httpx.AsyncClient() as client:
            response = await client.get(
                f"{server_url.rstrip('/')}/models",
                timeout=5.0,
            )
        if response.status_code == 200:
            data = response.json()
            models_data = data.get("data", [])
            return [m["id"] for m in models_data if "id" in m]
        return []
    except Exception:
        return []



@app.get("/api/glossaries")
async def list_glossary_presets():
    """List available glossary preset names (stems of glossaries/*.txt)."""
    return list_glossaries()


@app.get("/api/glossaries/{name}")
async def get_glossary_preset(name: str):
    """Return a glossary preset's injectable terms plus its raw annotated text."""
    try:
        terms = load_glossary_terms(name)
        raw = resolve_within_base(get_glossaries_dir(), f"{name}.txt").read_text(encoding="utf-8")
    except ValueError:
        return JSONResponse(
            status_code=400,
            content={"detail": "Invalid or unknown glossary name"},
        )
    return {"name": name, "terms": terms, "raw": raw}


@app.post("/api/logs/open")
async def open_logs():
    """Open the application log file in the default OS editor."""
    log_file = LOGS_DIR / "ocr_studio.log"
    if not log_file.exists():
        return JSONResponse(
            status_code=404,
            content={"detail": "Log file does not exist yet."}
        )
    
    try:
        if sys.platform == "win32":
            os.startfile(log_file)
        elif sys.platform == "darwin":
            subprocess.call(["open", str(log_file)])
        else:
            subprocess.call(["xdg-open", str(log_file)])
        return {"detail": "Log file opened."}
    except Exception as e:
        return JSONResponse(
            status_code=500,
            content={"detail": f"Failed to open log file: {e}"}
        )


# ---------------------------------------------------------------------------
# API Routes — Upload
# ---------------------------------------------------------------------------

@app.post("/api/upload", response_model=Union[UploadResponse, JobResponse])
async def upload_pdf(file: UploadFile = File(...)):
    """Upload a PDF or Markdown file and return its metadata or completed job status."""
    if not file.filename or not (file.filename.lower().endswith(".pdf") or file.filename.lower().endswith(".md")):
        return JSONResponse(
            status_code=400,
            content={"detail": "Only PDF or Markdown files are accepted"},
        )

    # Coerce to a bare filename (strip any browser-supplied path such as
    # "C:\fakepath\name.pdf") then validate containment inside the upload dir.
    safe_name = Path(file.filename).name
    is_markdown = safe_name.lower().endswith(".md")
    
    # For markdown files, enforce {stem}_FULL.md naming convention
    if is_markdown and not safe_name.endswith("_FULL.md"):
        stem = Path(safe_name).stem
        if stem.endswith("_FULL"):
            safe_name = f"{stem}.md"
        else:
            safe_name = f"{stem}_FULL.md"

    try:
        dest = resolve_within_base(get_output_dir() if is_markdown else get_upload_dir(), safe_name)
    except ValueError:
        return JSONResponse(
            status_code=400,
            content={"detail": "Invalid filename"},
        )
    with open(dest, "wb") as f:
        shutil.copyfileobj(file.file, f)

    if is_markdown:
        file_size = dest.stat().st_size
        with open(dest, "r", encoding="utf-8", errors="ignore") as f:
            content = f.read()
        page_markers = len(re.findall(r'<!--\s*PAGE', content))
        page_count = max(1, page_markers)
        
        logger.info("Uploaded Markdown %s (%d pages, %.1f MB)", safe_name, page_count, file_size / 1024 / 1024)
        
        job_id = str(uuid.uuid4())
        now_str = datetime.now(timezone.utc).isoformat()
        
        mock_job = JobResponse(
            job_id=job_id,
            status=JobStatus.COMPLETED,
            pdf_filename=file.filename,
            progress_percent=100.0,
            pages_completed=page_count,
            pages_total=page_count,
            pages_failed=0,
            output_filename=safe_name,
            created_at=now_str,
            completed_at=now_str
        )
        
        # Register in job_manager's history without spawning a worker — under the
        # lock and with the bounded-history trim, so repeated .md sideloads cannot
        # grow the job history unbounded (G-1).
        await job_manager.register_completed_job(mock_job)

        return mock_job

    # Count pages
    try:
        reader = PdfReader(str(dest))
        page_count = len(reader.pages)
    except Exception as e:
        os.remove(dest)
        return JSONResponse(
            status_code=400,
            content={"detail": f"Invalid PDF file: {e}"},
        )

    file_size = dest.stat().st_size

    logger.info("Uploaded %s (%d pages, %.1f MB)", safe_name, page_count, file_size / 1024 / 1024)

    return UploadResponse(
        filename=safe_name,
        file_size_bytes=file_size,
        page_count=page_count,
    )


# ---------------------------------------------------------------------------
# API Routes — Jobs
# ---------------------------------------------------------------------------

@app.post("/api/jobs", response_model=JobResponse)
async def create_job(req: JobCreateRequest):
    """Start a new OCR processing job."""
    try:
        pdf_path = resolve_within_base(get_upload_dir(), req.pdf_filename)
    except ValueError:
        return JSONResponse(
            status_code=400,
            content={"detail": "Invalid filename"},
        )

    if not pdf_path.exists():
        return JSONResponse(
            status_code=404,
            content={"detail": f"PDF file not found: {req.pdf_filename}. Upload it first."},
        )

    # Build settings from defaults + overrides
    settings = load_settings()
    overrides = req.model_dump(exclude={"pdf_filename"}, exclude_none=True)
    settings.update(overrides)

    try:
        job_id = await job_manager.create_job(
            pdf_path=str(pdf_path),
            pdf_filename=req.pdf_filename,
            settings=settings,
        )
    except Exception as e:
        return JSONResponse(
            status_code=500,
            content={"detail": str(e)},
        )

    job = job_manager.get_job(job_id)
    return job


@app.get("/api/jobs", response_model=list[JobResponse])
async def list_jobs():
    """List all recent jobs."""
    return job_manager.get_all_jobs()


@app.get("/api/jobs/{job_id}", response_model=JobResponse)
async def get_job(job_id: str):
    """Get status of a specific job."""
    job = job_manager.get_job(job_id)
    if not job:
        return JSONResponse(
            status_code=404,
            content={"detail": f"Job not found: {job_id}"},
        )
    return job


@app.post("/api/jobs/{job_id}/cancel")
async def cancel_job(job_id: str):
    """Cancel a running job."""
    success = job_manager.cancel_job(job_id)
    if not success:
        return JSONResponse(
            status_code=404,
            content={"detail": f"Job not found or not in a cancellable state: {job_id}"},
        )
    return {"detail": f"Job {job_id} cancellation requested."}


@app.delete("/api/jobs")
async def clear_jobs():
    """Clear all job history."""
    job_manager.clear_jobs()
    return {"detail": "Job history cleared."}


# ---------------------------------------------------------------------------
# API Routes — Download
# ---------------------------------------------------------------------------

@app.get("/api/download/{filename}")
async def download_file(filename: str, fmt: Optional[str] = None):
    """Download a completed Markdown file, optionally converting to HTML or DOCX."""
    try:
        file_path = resolve_within_base(get_output_dir(), filename)
    except ValueError:
        return JSONResponse(
            status_code=400,
            content={"detail": "Invalid filename"},
        )

    if not file_path.exists():
        return JSONResponse(
            status_code=404,
            content={"detail": f"File not found: {filename}"},
        )

    # If no format override is requested, return the raw markdown file
    if not fmt:
        return FileResponse(
            path=str(file_path),
            filename=filename,
            media_type="text/markdown",
            headers={"Cache-Control": "no-cache, no-store, must-revalidate", "Pragma": "no-cache", "Expires": "0"}
        )

    fmt = fmt.lower()
    if fmt not in ("html", "docx"):
        return JSONResponse(
            status_code=400,
            content={"detail": f"Unsupported export format: {fmt}. Only 'html' and 'docx' are supported."},
        )

    # Derive export path
    suffix = f".{fmt}"
    export_path = file_path.with_suffix(suffix)

    # Caching: convert only if export file doesn't exist or is older than source markdown
    if not export_path.exists() or export_path.stat().st_mtime < file_path.stat().st_mtime:
        from backend.export_utils import convert_markdown_to_html, convert_markdown_to_docx
        
        success = False
        if fmt == "html":
            success = convert_markdown_to_html(file_path, export_path)
        elif fmt == "docx":
            success = convert_markdown_to_docx(file_path, export_path)

        if not success:
            return JSONResponse(
                status_code=500,
                content={"detail": f"Failed to convert document to {fmt.upper()} format."},
            )

    media_types = {
        "html": "text/html",
        "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    }

    return FileResponse(
        path=str(export_path),
        filename=export_path.name,
        media_type=media_types[fmt],
    )


@app.put("/api/download/{filename}")
async def save_edited_file(filename: str, payload: SaveFileRequest):
    """Save edited Markdown text back to the output directory."""
    try:
        file_path = resolve_within_base(get_output_dir(), filename)
    except ValueError:
        return JSONResponse(
            status_code=400,
            content={"detail": "Invalid filename"},
        )

    if not file_path.parent.exists():
        file_path.parent.mkdir(parents=True, exist_ok=True)

    content = payload.content
    with open(file_path, "w", encoding="utf-8") as f:
        f.write(content)
    
    # Invalidate converted formats cache
    for suffix in (".html", ".docx"):
        export_path = file_path.with_suffix(suffix)
        if export_path.exists():
            try:
                os.remove(export_path)
            except Exception:
                pass
    return {"detail": "File saved successfully."}


@app.post("/api/jobs/reprocess-zone")
async def reprocess_zone(req: ZoneReprocessRequest):
    """Re-run OCR processing on a specific cropped zone of a page."""
    job = job_manager.get_job(req.job_id)
    if not job:
        return JSONResponse(
            status_code=404,
            content={"detail": f"Job not found: {req.job_id}"},
        )

    try:
        pdf_path = resolve_within_base(get_upload_dir(), job.pdf_filename)
    except ValueError:
        return JSONResponse(
            status_code=400,
            content={"detail": "Invalid filename"},
        )
    if not pdf_path.exists():
        return JSONResponse(
            status_code=404,
            content={"detail": f"Source PDF file not found: {job.pdf_filename}"},
        )

    settings = load_settings()

    try:
        page_result = await process_cropped_zone(
            pdf_path=str(pdf_path),
            page_num=req.page_num,
            x=req.x,
            y=req.y,
            width=req.width,
            height=req.height,
            settings=settings,
        )

        if not page_result.success:
            return JSONResponse(
                status_code=500,
                content={"detail": f"OCR failed for cropped zone: {page_result.error_message}"},
            )

        # Update in-memory job status with corrected page confidence if logprobs available
        if page_result.confidence_score is not None:
            job.page_confidence[str(req.page_num)] = page_result.confidence_score
        if page_result.token_logprobs is not None:
            job.page_token_logprobs[str(req.page_num)] = page_result.token_logprobs

        # Return the new OCR text, confidence and logprobs
        return {
            "page_num": req.page_num,
            "natural_text": page_result.response.natural_text,
            "confidence_score": page_result.confidence_score,
            "token_logprobs": page_result.token_logprobs,
        }
    except Exception as e:
        logger.exception("Failed to re-process zone")
        return JSONResponse(
            status_code=500,
            content={"detail": f"Error reprocessing zone: {str(e)}"},
        )

# Regex to strip inline chain-of-thought that some reasoning models (e.g. Qwen3)
# leak into the main content field wrapped in <think>...</think>.
_THINK_RE = re.compile(r"<think>.*?</think>", re.DOTALL | re.IGNORECASE)
_THINK_OPEN_RE = re.compile(r"<think>.*$", re.DOTALL | re.IGNORECASE)


def _strip_reasoning(text: str) -> str:
    """Remove <think> reasoning blocks (complete or truncated/unclosed) from model output."""
    if not text:
        return ""
    text = _THINK_RE.sub("", text)       # complete <think>...</think> blocks (multiline)
    text = _THINK_OPEN_RE.sub("", text)  # unclosed <think> (truncation) -> drop remainder
    return text.strip()


def _chunk_text(text: str, max_chars: int = 6000) -> list[str]:
    """Split text into manageable chunks, prioritizing page break comments."""
    if not text:
        return []
        
    # Split by the page markers, but keeping the page markers
    # A page marker is like <!-- PAGE 001 -->
    pattern = r'(<!--\s*PAGE\s+\d+\s*-->)'
    parts = re.split(pattern, text)
    
    if len(parts) > 1:
        # We have page markers!
        chunks = []
        current_chunk = []
        current_len = 0
        
        # Add prefix
        if parts[0]:
            current_chunk.append(parts[0])
            current_len += len(parts[0])
            
        for i in range(1, len(parts), 2):
            marker = parts[i]
            content = parts[i+1] if (i+1) < len(parts) else ""
            item_len = len(marker) + len(content)
            
            # If item itself is extremely long, we may need to split it
            if item_len > max_chars:
                # Flush current chunk first if not empty
                if current_chunk:
                    chunks.append("".join(current_chunk))
                    current_chunk = []
                    current_len = 0
                # Split this large item by paragraphs/lines
                sub_chunks = _chunk_by_delimiters(marker + content, max_chars)
                chunks.extend(sub_chunks)
            else:
                if current_chunk and (current_len + item_len > max_chars):
                    chunks.append("".join(current_chunk))
                    current_chunk = []
                    current_len = 0
                current_chunk.append(marker + content)
                current_len += item_len
                
        if current_chunk:
            chunks.append("".join(current_chunk))
        return chunks
    else:
        # No page markers, split by paragraphs/lines
        return _chunk_by_delimiters(text, max_chars)


def _chunk_by_delimiters(text: str, max_chars: int) -> list[str]:
    """Split text into chunks using paragraph and line breaks."""
    paragraphs = text.split("\n\n")
    chunks = []
    current_chunk = []
    current_len = 0
    
    for para in paragraphs:
        # Keep paragraph separator when reconstructing
        item = para + "\n\n"
        if len(item) > max_chars:
            # If paragraph itself is too long, split by lines
            if current_chunk:
                chunks.append("".join(current_chunk))
                current_chunk = []
                current_len = 0
            lines = para.split("\n")
            for line in lines:
                sub_item = line + "\n"
                if len(sub_item) > max_chars:
                    # If line itself is too long, split by character count (worst case fallback)
                    if current_chunk:
                        chunks.append("".join(current_chunk))
                        current_chunk = []
                        current_len = 0
                    for k in range(0, len(sub_item), max_chars):
                        chunks.append(sub_item[k:k+max_chars])
                else:
                    if current_chunk and (current_len + len(sub_item) > max_chars):
                        chunks.append("".join(current_chunk))
                        current_chunk = []
                        current_len = 0
                    current_chunk.append(sub_item)
                    current_len += len(sub_item)
        else:
            if current_chunk and (current_len + len(item) > max_chars):
                chunks.append("".join(current_chunk))
                current_chunk = []
                current_len = 0
            current_chunk.append(item)
            current_len += len(item)
            
    if current_chunk:
        chunks.append("".join(current_chunk))
    return [c.rstrip("\n") for c in chunks if c.strip()]


class _TranslateError(Exception):
    """Raised inside the translation stream to surface a specific error detail."""


# Any <!-- PAGE NNN --> marker, used both to split pages and to scrub markers that
# a mis-behaving model might leak into its output.
_PAGE_MARKER_RE = re.compile(r'<!--\s*PAGE\s+\d+\s*-->')


def _split_pages(text: str) -> list[tuple[str, str]]:
    """Split marker-bearing markdown into ordered ``(marker, content)`` units.

    The ``<!-- PAGE NNN -->`` marker string is preserved verbatim; ``content`` is
    the text between markers. Any preamble before the first marker is returned as a
    unit with an empty marker. Reassembling ``marker + content`` in order therefore
    reproduces the EXACT page structure — and because only ``content`` is ever sent
    to the model (never the marker), the model cannot invent, duplicate, or drift
    pages the way it did when asked to echo an incrementing marker.
    """
    parts = re.split(r'(<!--\s*PAGE\s+\d+\s*-->)', text)
    units: list[tuple[str, str]] = []
    if parts and parts[0].strip():           # preamble before the first marker
        units.append(("", parts[0]))
    for i in range(1, len(parts), 2):
        marker = parts[i]
        content = parts[i + 1] if (i + 1) < len(parts) else ""
        units.append((marker, content))
    return units


@app.post("/api/jobs/translate")
async def translate_document(req: TranslateRequest, request: Request):
    """Translate classical Chinese text to English, streaming per-page progress.

    Structure-preserving translation. When the input contains ``<!-- PAGE NNN -->``
    markers, the document is split into per-page units; only each page's *content*
    is sent to the model (never the marker), and the original markers are
    reassembled deterministically in code. This makes it structurally impossible
    for the model to invent phantom pages or drift content across page boundaries —
    previously, asking the model to echo an incrementing marker triggered a
    repetition loop that emitted pages 009–100 of boilerplate. Identical page
    contents (e.g. a repeated title header) are translated once and memoized (an
    efficiency optimization that does not change the output), and any stray marker
    the model leaks into its output is scrubbed. Translation is faithful: repeated
    or blank pages in the source are reproduced as-is (de-duplicating repeated
    scanned leaves is the upstream OCR's job, not the translator's).

    Marker-less input falls back to size-based chunking.

    Returns Server-Sent Events (``text/event-stream``):
      - ``{"status":"processing","current_chunk":i,"total_chunks":N,"progress_pct":pct}``
        emitted at the START of each page/chunk.
      - ``{"status":"completed","translated_text":"..."}`` on success.
      - ``{"status":"error","detail":"..."}`` on failure (surfaced in-band because
        the HTTP 200 status is already sent once streaming begins).

    The frontend retains ownership of persisting the translation to a separate
    ``_EN.md`` file (H-9), so this endpoint streams text only and never writes to disk.
    """
    settings = load_settings()
    server_url = settings.get("server_url", "http://localhost:1234/v1")

    # Priority: Translation Model -> Fallback to OCR Model
    model = settings.get("translation_model")
    if not model:
        model = settings.get("model", "")

    completion_url = f"{server_url.rstrip('/')}/chat/completions"
    system_prompt = (
        "You are an expert translator specializing in translating Classical Chinese philosophical, historical, and archival texts into natural English. "
        "Translate the provided Classical Chinese text into clear, academic English.\n\n"
        "Rules:\n"
        "1. Return ONLY the English translation of the text you are given.\n"
        "2. Do NOT add explanations, prefaces, page numbers, headers, HTML comments, or original Chinese characters.\n"
        "3. Do NOT repeat yourself: translate the given text exactly once and then stop.\n"
        "4. Preserve paragraph and line breaks.\n\n"
        "Examples:\n"
        "Input: 剛柔者立本者也變通者趣時者也\n"
        "Output: Hardness and softness are the established foundation; change and continuity are the adaptation to time.\n\n"
        "Input: 吉凶者貞勝者也\n"
        "Output: Good fortune and misfortune are determined by perseverance."
    )

    # Structure-preserving unit split: one unit per page when markers exist,
    # otherwise size-based chunks (no markers to loop on).
    if _PAGE_MARKER_RE.search(req.content):
        units = _split_pages(req.content)
    else:
        units = [("", c) for c in _chunk_text(req.content)]

    async def event_stream():
        def sse(payload: dict) -> bytes:
            return b"data: " + orjson.dumps(payload) + b"\n\n"

        # Empty input is a well-formed (trivial) translation, not an error.
        if not units:
            yield sse({"status": "completed", "translated_text": ""})
            return

        cache: dict[str, str] = {}

        async def translate_content(client, content: str) -> str:
            """Translate one marker-free content string (sub-chunking if large),
            memoized on the stripped content. Raises _TranslateError on failure."""
            stripped = content.strip()
            if not stripped:
                return ""
            if stripped in cache:
                return cache[stripped]

            sub_parts = _chunk_by_delimiters(stripped, 6000) if len(stripped) > 6000 else [stripped]
            out_parts: list[str] = []
            for part in sub_parts:
                payload = {
                    "model": model,
                    "messages": [
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": f"Please translate this content now:\n\n{part}"}
                    ],
                    "temperature": 0.3,
                    "max_tokens": 8000,
                    # Suppress the repetition/degeneration loops seen on repetitive
                    # classical text — defense-in-depth alongside marker isolation.
                    "frequency_penalty": 0.3,
                    "presence_penalty": 0.3,
                }
                res = await client.post(completion_url, json=payload)
                if res.status_code != 200:
                    logger.error("LM Studio translation endpoint returned error status: %d - %s", res.status_code, res.text)
                    try:
                        error_json = res.json()
                        if "error" in error_json and "message" in error_json["error"]:
                            detail = f"LM Studio: {error_json['error']['message']}"
                        else:
                            detail = f"LM Studio error: {res.text}"
                    except Exception:
                        detail = f"LM Studio returned status {res.status_code}: {res.text}"
                    raise _TranslateError(detail)

                data = res.json()
                message = data["choices"][0]["message"]
                translated = _strip_reasoning(message.get("content") or "")
                # Scrub any page marker the model leaked into its output, so the
                # only markers in the final document are the ones we reassemble.
                translated = _PAGE_MARKER_RE.sub("", translated).strip()
                if not translated:
                    logger.error("Translation returned no content after stripping reasoning (model=%s).", model)
                    raise _TranslateError("Translation model returned no content.")
                out_parts.append(translated)

            result = "\n\n".join(out_parts)
            cache[stripped] = result
            return result

        total = len(units)
        translated_units: list[str] = []
        logger.info("Starting SSE translation of %d page unit(s)...", total)

        try:
            async with httpx.AsyncClient(timeout=600.0) as client:
                for idx, (marker, content) in enumerate(units):
                    # Abort promptly if the client (browser) disconnected — e.g. the
                    # user hit Cancel. Checked between pages: a page whose call is
                    # already in flight finishes first, then the loop returns before
                    # starting the next call. No "completed" event is emitted on abort.
                    if await request.is_disconnected():
                        logger.info("Client disconnected; aborting translation before unit %d/%d.", idx + 1, total)
                        return

                    # Progress at the START of each page so the UI updates immediately.
                    yield sse({
                        "status": "processing",
                        "current_chunk": idx + 1,
                        "total_chunks": total,
                        "progress_pct": round(idx / total * 100, 1),
                    })

                    # Faithful translation: every page is translated as the source
                    # has it (repeats included). Duplicate/blank pages in the output
                    # mirror duplicate/blank pages in the source — the right place to
                    # de-duplicate repeated scanned leaves is the upstream OCR, not
                    # here. (Memoization still avoids re-calling the model for
                    # identical content without changing the output.)
                    try:
                        translated = await translate_content(client, content)
                    except _TranslateError as te:
                        yield sse({"status": "error", "detail": str(te)})
                        return

                    # Reassemble the ORIGINAL marker verbatim in code; the model
                    # never saw it, so the page structure is exact.
                    translated_units.append(f"{marker}\n\n{translated}" if marker else translated)

                yield sse({"status": "completed", "translated_text": "\n\n".join(translated_units)})

        except Exception as e:
            logger.exception("Failed to translate markdown content")
            yield sse({"status": "error", "detail": f"Translation failed: {str(e)}"})

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.get("/api/pdf/{filename}/page/{page_number}/image")
async def get_pdf_page_image(filename: str, page_number: int):
    """Render a specific PDF page to JPEG and return it."""
    try:
        pdf_path = resolve_within_base(get_upload_dir(), filename)
    except ValueError:
        return JSONResponse(
            status_code=400,
            content={"detail": "Invalid filename"},
        )
    if not pdf_path.exists():
        return JSONResponse(
            status_code=404,
            content={"detail": f"PDF file not found: {filename}"},
        )

    if page_number < 1:
        return JSONResponse(
            status_code=400,
            content={"detail": "Page number must be >= 1"},
        )

    try:
        import pypdfium2 as pdfium
        import io
        from PIL import Image

        doc = pdfium.PdfDocument(str(pdf_path))
        total_pages = len(doc)
        if page_number > total_pages:
            doc.close()
            return JSONResponse(
                status_code=400,
                content={"detail": f"Page number {page_number} exceeds total pages {total_pages}"},
            )

        # Retrieve the page (0-indexed in pypdfium2)
        page = doc[page_number - 1]
        
        # Render page to bitmap. 
        # Calculate scale to target ~2400px longest dimension for sharp display when zooming
        width, height = page.get_size()
        scale = 2400 / max(width, height)
        
        bitmap = page.render(
            scale=scale,
            rotation=0,
            crop=(0, 0, 0, 0),
        )
        
        # Convert bitmap to PIL Image
        pil_img = bitmap.to_pil()
        
        # Save to BytesIO as JPEG
        img_byte_arr = io.BytesIO()
        pil_img.save(img_byte_arr, format='JPEG', quality=85)
        jpeg_bytes = img_byte_arr.getvalue()

        # Free resources
        bitmap.close()
        page.close()
        doc.close()

        # Set Cache-Control header to cache for 1 day
        headers = {
            "Cache-Control": "public, max-age=86400"
        }

        return Response(content=jpeg_bytes, media_type="image/jpeg", headers=headers)
    except Exception as e:
        logger.error("Failed to render PDF page %d: %s", page_number, e, exc_info=True)
        return JSONResponse(
            status_code=500,
            content={"detail": f"Failed to render page: {e}"},
        )


# ---------------------------------------------------------------------------
# WebSocket — Real-time progress
# ---------------------------------------------------------------------------

@app.websocket("/ws/progress")
async def websocket_progress(ws: WebSocket):
    """WebSocket endpoint for real-time job progress updates."""
    await ws.accept()
    await job_manager.register_client(ws)

    try:
        # Keep the connection alive by reading messages (client may send pings)
        while True:
            await ws.receive_text()
    except WebSocketDisconnect:
        pass
    finally:
        await job_manager.unregister_client(ws)


# ---------------------------------------------------------------------------
# Static file serving (frontend)
# ---------------------------------------------------------------------------

# Mount frontend as static files — must be last to not shadow API routes
frontend_dir = PROJECT_ROOT / "frontend"
if frontend_dir.exists():
    app.mount("/", StaticFiles(directory=str(frontend_dir), html=True), name="frontend")
else:
    @app.get("/")
    async def root():
        return {"message": "OCR Studio backend is running. Frontend not found at /frontend."}
