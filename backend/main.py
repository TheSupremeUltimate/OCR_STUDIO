"""
OCR Studio — FastAPI Application

Main entry point for the OCR Studio backend. Serves the REST API,
WebSocket endpoint, and static frontend files.
"""

import logging
import os
import shutil
import time
import sys
import subprocess
from pathlib import Path
from typing import Optional

import httpx
import orjson
from fastapi import FastAPI, File, UploadFile, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, Response
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
)
from backend.job_manager import JobManager
from backend.models import (
    JobCreateRequest,
    JobResponse,
    ServerHealthResponse,
    SettingsResponse,
    SettingsUpdateRequest,
    UploadResponse,
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

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
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

@app.post("/api/upload", response_model=UploadResponse)
async def upload_pdf(file: UploadFile = File(...)):
    """Upload a PDF file and return its metadata."""
    if not file.filename or not file.filename.lower().endswith(".pdf"):
        return JSONResponse(
            status_code=400,
            content={"detail": "Only PDF files are accepted"},
        )

    # Save the uploaded file
    dest = get_upload_dir() / file.filename
    with open(dest, "wb") as f:
        shutil.copyfileobj(file.file, f)

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

    logger.info("Uploaded %s (%d pages, %.1f MB)", file.filename, page_count, file_size / 1024 / 1024)

    return UploadResponse(
        filename=file.filename,
        file_size_bytes=file_size,
        page_count=page_count,
    )


# ---------------------------------------------------------------------------
# API Routes — Jobs
# ---------------------------------------------------------------------------

@app.post("/api/jobs", response_model=JobResponse)
async def create_job(req: JobCreateRequest):
    """Start a new OCR processing job."""
    pdf_path = get_upload_dir() / req.pdf_filename

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
    file_path = get_output_dir() / filename

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


@app.get("/api/pdf/{filename}/page/{page_number}/image")
async def get_pdf_page_image(filename: str, page_number: int):
    """Render a specific PDF page to JPEG and return it."""
    pdf_path = get_upload_dir() / filename
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
        # Calculate scale to target ~1200px longest dimension for sharp display
        width, height = page.get_size()
        scale = 1200 / max(width, height)
        
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
