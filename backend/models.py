"""
OCR Studio — Pydantic Models

Request/response schemas for the FastAPI endpoints.
"""

from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field


class JobStatus(str, Enum):
    """Lifecycle states for an OCR job."""
    QUEUED = "queued"
    PROCESSING = "processing"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


# ---------------------------------------------------------------------------
# Request models
# ---------------------------------------------------------------------------

class JobCreateRequest(BaseModel):
    """Request to start a new OCR job."""
    pdf_filename: str = Field(..., description="Name of the previously uploaded PDF file")
    server_url: Optional[str] = Field(None, description="Override LM Studio server URL")
    model: Optional[str] = Field(None, description="Override model name")
    workers: Optional[int] = Field(None, ge=1, le=8, description="Number of concurrent workers")
    pages_per_group: Optional[int] = Field(None, ge=1, le=50, description="Pages per processing group")
    target_longest_image_dim: Optional[int] = Field(None, ge=256, le=4096, description="Target longest image dimension in pixels")
    max_page_retries: Optional[int] = Field(None, ge=0, le=10, description="Max retries per page")
    page_range: Optional[str] = Field(None, description="Page range to process (e.g. '1-5, 8')")


class SettingsUpdateRequest(BaseModel):
    """Request to update application settings."""
    server_url: Optional[str] = None
    model: Optional[str] = None
    workers: Optional[int] = Field(None, ge=1, le=8)
    pages_per_group: Optional[int] = Field(None, ge=1, le=50)
    target_longest_image_dim: Optional[int] = Field(None, ge=256, le=4096)
    max_page_retries: Optional[int] = Field(None, ge=0, le=10)
    output_dir: Optional[str] = None
    page_range: Optional[str] = None


# ---------------------------------------------------------------------------
# Response models
# ---------------------------------------------------------------------------

class UploadResponse(BaseModel):
    """Response after uploading a PDF."""
    filename: str
    file_size_bytes: int
    page_count: int
    message: str = "PDF uploaded successfully"


class JobResponse(BaseModel):
    """Response for job status queries."""
    job_id: str
    status: JobStatus
    pdf_filename: str
    progress_percent: float = Field(0.0, ge=0, le=100)
    pages_completed: int = 0
    pages_total: int = 0
    pages_failed: int = 0
    output_filename: Optional[str] = None
    error_message: Optional[str] = None
    created_at: str = ""
    completed_at: Optional[str] = None
    page_confidence: dict[str, Optional[float]] = Field(default_factory=dict)


class SettingsResponse(BaseModel):
    """Response containing current application settings."""
    server_url: str
    model: str
    workers: int
    pages_per_group: int
    target_longest_image_dim: int
    max_page_retries: int
    max_tokens: int
    output_dir: str
    page_range: str


class ServerHealthResponse(BaseModel):
    """Response for LM Studio server health check."""
    reachable: bool
    model_loaded: Optional[str] = None
    response_time_ms: Optional[float] = None
    error: Optional[str] = None


# ---------------------------------------------------------------------------
# WebSocket message models
# ---------------------------------------------------------------------------

class ProgressMessage(BaseModel):
    """Real-time progress update sent over WebSocket."""
    job_id: str
    event: str  # "page_start", "page_complete", "page_failed", "job_complete", "job_failed"
    page_num: Optional[int] = None
    pages_total: Optional[int] = None
    pages_completed: Optional[int] = None
    pages_failed: Optional[int] = None
    progress_percent: float = 0.0
    message: str = ""
    eta_seconds: Optional[float] = None
    confidence: Optional[float] = None
