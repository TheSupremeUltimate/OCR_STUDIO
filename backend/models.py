"""
OCR Studio — Pydantic Models

Request/response schemas for the FastAPI endpoints.
"""

from enum import Enum
from typing import Optional, List, Dict, Any

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
    custom_glossary: Optional[str] = Field(None, description="Custom glossary for document-specific terminology")
    strict_mode: Optional[bool] = Field(None, description="Enable strict archival mode (exact 1:1 transcription)")
    reading_direction: Optional[str] = Field(None, description="Reading direction override (Default, Vertical RTL, Horizontal LTR)")
    document_structure: Optional[str] = Field(None, description="Document structure classification (Standard, Main Text + Interline Commentary)")
    binarize: Optional[bool] = Field(None, description="Enable binarization filter (B/W)")
    high_contrast: Optional[bool] = Field(None, description="Enable high contrast filter")
    despeckle: Optional[bool] = Field(None, description="Enable despeckle (median filter)")
    consensus_mode: Optional[bool] = Field(None, description="Enable consensus voting mode (768px, 1288px, 2048px)")


class SettingsUpdateRequest(BaseModel):
    """Request to update application settings."""
    server_url: Optional[str] = None
    model: Optional[str] = None
    translation_model: Optional[str] = None
    workers: Optional[int] = Field(None, ge=1, le=8)
    pages_per_group: Optional[int] = Field(None, ge=1, le=50)
    target_longest_image_dim: Optional[int] = Field(None, ge=256, le=4096)
    max_page_retries: Optional[int] = Field(None, ge=0, le=10)
    max_tokens: Optional[int] = Field(None, ge=256, le=16000)
    output_dir: Optional[str] = None
    page_range: Optional[str] = None
    custom_glossary: Optional[str] = None
    strict_mode: Optional[bool] = None
    reading_direction: Optional[str] = None
    document_structure: Optional[str] = None
    binarize: Optional[bool] = None
    high_contrast: Optional[bool] = None
    despeckle: Optional[bool] = None
    consensus_mode: Optional[bool] = None


class SaveFileRequest(BaseModel):
    """Request body for saving edited Markdown back to disk (debounced auto-save)."""
    content: str = Field(..., description="Full Markdown text content to persist")


class ZoneReprocessRequest(BaseModel):
    """Request to re-run OCR on a specific cropped zone of a page."""
    job_id: str = Field(..., description="ID of the active or completed job")
    page_num: int = Field(..., description="1-indexed page number to reprocess")
    x: float = Field(..., description="Normalized x coordinate (0.0 to 1.0)")
    y: float = Field(..., description="Normalized y coordinate (0.0 to 1.0)")
    width: float = Field(..., description="Normalized width (0.0 to 1.0)")
    height: float = Field(..., description="Normalized height (0.0 to 1.0)")


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
    page_token_logprobs: dict[str, Optional[list[dict]]] = Field(default_factory=dict)
    total_runtime: Optional[float] = None
    average_confidence: Optional[float] = None
    total_retries: int = 0


class SettingsResponse(BaseModel):
    """Response containing current application settings."""
    server_url: str
    model: str
    translation_model: Optional[str] = None
    workers: int
    pages_per_group: int
    target_longest_image_dim: int
    max_page_retries: int
    max_tokens: int
    output_dir: str
    page_range: str
    custom_glossary: str
    strict_mode: bool
    reading_direction: str
    document_structure: str
    binarize: bool
    high_contrast: bool
    despeckle: bool
    consensus_mode: bool = False


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
    total_runtime: Optional[float] = None
    average_confidence: Optional[float] = None
    total_retries: Optional[int] = None
    token_logprobs: Optional[List[Dict[str, Any]]] = None


class TranslateRequest(BaseModel):
    """Request model for document translation."""
    content: str


