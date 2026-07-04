"""
OCR Studio — Core OCR Engine

Processes a PDF file page-by-page through a vision language model (LM Studio)
and produces a merged Markdown output file.

Adapted from OlmOCR (Apache 2.0 License) — https://github.com/allenai/olmocr
Key functions adapted: render_pdf_to_base64png, build_page_query, response parsing.
"""

import asyncio
import base64
import json
import logging
import math
import os
import subprocess
import time
from dataclasses import dataclass
from io import BytesIO
from pathlib import Path
from typing import Any, Callable, Coroutine, Dict, Optional

import httpx
import yaml
from PIL import Image
from pypdf import PdfReader

logger = logging.getLogger("ocr_studio.engine")

# Temperature values for retry attempts (from OlmOCR)
TEMPERATURE_BY_ATTEMPT = [0.1, 0.7, 0.8, 0.9, 1.0]


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class PageResponse:
    """Structured response from the vision model for a single page.
    Mirrors OlmOCR's PageResponse dataclass."""
    primary_language: Optional[str]
    is_rotation_valid: bool
    rotation_correction: int
    is_table: bool
    is_diagram: bool
    natural_text: Optional[str]


@dataclass
class PageResult:
    """Result of processing a single page."""
    page_num: int
    response: PageResponse
    input_tokens: int = 0
    output_tokens: int = 0
    is_fallback: bool = False
    success: bool = True
    error_message: Optional[str] = None
    confidence_score: Optional[float] = None


# Progress callback type
ProgressCallback = Callable[[int, int, str, Optional[str], Optional[float], Optional[float]], Coroutine[Any, Any, None]]
# Signature: async callback(page_num, total_pages, event, message, eta, confidence)


# ---------------------------------------------------------------------------
# PDF Rendering (adapted from OlmOCR renderpdf.py)
# ---------------------------------------------------------------------------

def get_pdf_media_box_width_height(local_pdf_path: str, page_num: int) -> tuple[float, float]:
    """Get the MediaBox dimensions for a specific page using pdfinfo."""
    command = [
        "pdfinfo", "-f", str(page_num), "-l", str(page_num),
        "-box", "-enc", "UTF-8", local_pdf_path
    ]
    result = subprocess.run(
        command,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        encoding="utf-8",
        errors="ignore"
    )

    if result.returncode != 0:
        raise ValueError(f"Error running pdfinfo: {result.stderr}")

    for line in result.stdout.splitlines():
        if "MediaBox" in line:
            media_box_str = line.split(":")[1].strip().split()
            media_box = [float(x) for x in media_box_str]
            return abs(media_box[0] - media_box[2]), abs(media_box[3] - media_box[1])

    raise ValueError("MediaBox not found in the PDF info.")


def render_pdf_to_base64png(local_pdf_path: str, page_num: int, target_longest_image_dim: int = 768) -> str:
    """Render a single PDF page to a base64-encoded PNG string using pdftoppm."""
    longest_dim = max(get_pdf_media_box_width_height(local_pdf_path, page_num))

    pdftoppm_result = subprocess.run(
        [
            "pdftoppm", "-png",
            "-f", str(page_num),
            "-l", str(page_num),
            "-r", str(target_longest_image_dim * 72 / longest_dim),
            local_pdf_path,
        ],
        timeout=120,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )

    if pdftoppm_result.returncode != 0:
        raise RuntimeError(f"pdftoppm failed: {pdftoppm_result.stderr.decode()}")

    return base64.b64encode(pdftoppm_result.stdout).decode("utf-8")


# ---------------------------------------------------------------------------
# Prompt building (adapted from OlmOCR prompts.py)
# ---------------------------------------------------------------------------

def build_prompt() -> str:
    """Build the vision model prompt (OlmOCR v4 no-anchoring YAML prompt)."""
    return (
        "Attached is one page of a document that you must process. "
        "Just return the plain text representation of this document as if you were reading it naturally. "
        "Convert equations to LateX and tables to HTML.\n"
        "If there are any figures or charts, label them with the following markdown syntax "
        "![Alt text describing the contents of the figure](page_startx_starty_width_height.png)\n"
        "Return your output as markdown, with a front matter section on top specifying values for the "
        "primary_language, is_rotation_valid, rotation_correction, is_table, and is_diagram parameters."
    )


async def build_page_query(
    local_pdf_path: str,
    page: int,
    target_longest_image_dim: int,
    model_name: str,
    max_tokens: int = 1600,
    temperature: float = 0.1,
) -> dict:
    """Construct the OpenAI-compatible chat completion request for a single page."""

    # Render in a thread to avoid blocking the event loop
    image_base64 = await asyncio.to_thread(
        render_pdf_to_base64png, local_pdf_path, page, target_longest_image_dim
    )

    return {
        "model": model_name,
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": build_prompt()},
                    {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{image_base64}"}},
                ],
            }
        ],
        "max_tokens": max_tokens,
        "temperature": temperature,
        "logprobs": True,
    }


# ---------------------------------------------------------------------------
# Response parsing (adapted from OlmOCR front_matter.py)
# ---------------------------------------------------------------------------

def parse_model_response(response_text: str) -> PageResponse:
    """
    Parse the model's YAML front matter + markdown response into a PageResponse.
    Adapted from OlmOCR's FrontMatterParser.
    """
    front_matter = {}
    natural_text = response_text.strip()

    if response_text.startswith("---\n"):
        end_index = response_text.find("\n---", 4)
        if end_index != -1:
            front_matter_str = response_text[4:end_index]
            natural_text = response_text[end_index + 4:].strip()
            try:
                front_matter = yaml.safe_load(front_matter_str) or {}
            except yaml.YAMLError as e:
                logger.warning("Failed to parse YAML front matter: %s", e)

    # Extract fields with safe defaults
    primary_language = front_matter.get("primary_language", None)
    if isinstance(primary_language, bool):
        primary_language = None

    is_rotation_valid = front_matter.get("is_rotation_valid", True)
    if isinstance(is_rotation_valid, str):
        is_rotation_valid = is_rotation_valid.lower() == "true"

    rotation_correction = int(front_matter.get("rotation_correction", 0))
    if rotation_correction not in (0, 90, 180, 270):
        rotation_correction = 0

    is_table = front_matter.get("is_table", False)
    if isinstance(is_table, str):
        is_table = is_table.lower() == "true"

    is_diagram = front_matter.get("is_diagram", False)
    if isinstance(is_diagram, str):
        is_diagram = is_diagram.lower() == "true"

    return PageResponse(
        primary_language=primary_language,
        is_rotation_valid=is_rotation_valid,
        rotation_correction=rotation_correction,
        is_table=is_table,
        is_diagram=is_diagram,
        natural_text=natural_text if natural_text else None,
    )


# ---------------------------------------------------------------------------
# Single page processing with retry
# ---------------------------------------------------------------------------

async def process_single_page(
    http_client: httpx.AsyncClient,
    pdf_path: str,
    page_num: int,
    server_url: str,
    model: str,
    target_dim: int,
    max_tokens: int,
    max_retries: int,
) -> PageResult:
    """
    Process a single PDF page: render → send to model → parse response.
    Includes retry logic with escalating temperature.
    """
    completion_url = f"{server_url.rstrip('/')}/chat/completions"

    for attempt in range(max_retries + 1):
        temp_idx = min(attempt, len(TEMPERATURE_BY_ATTEMPT) - 1)
        temperature = TEMPERATURE_BY_ATTEMPT[temp_idx]

        try:
            query = await build_page_query(
                pdf_path, page_num, target_dim, model, max_tokens, temperature
            )

            response = await http_client.post(
                completion_url,
                json=query,
                timeout=300.0,
            )

            if response.status_code != 200:
                logger.warning(
                    "Server returned %d for page %d attempt %d: %s",
                    response.status_code, page_num, attempt,
                    response.text[:300]
                )
                continue

            data = response.json()

            # Check for valid completion
            if data["choices"][0].get("finish_reason") != "stop":
                logger.warning("Incomplete response for page %d attempt %d", page_num, attempt)
                continue

            model_text = data["choices"][0]["message"]["content"]
            page_response = parse_model_response(model_text)

            # Calculate confidence score if logprobs are available
            confidence_score = None
            choice = data["choices"][0]
            if "logprobs" in choice and choice["logprobs"] is not None:
                logprobs_data = choice["logprobs"]
                content_logprobs = logprobs_data.get("content")
                if content_logprobs:
                    probs = []
                    for t in content_logprobs:
                        lp = t.get("logprob")
                        if lp is not None:
                            try:
                                probs.append(math.exp(float(lp)))
                            except (ValueError, OverflowError):
                                pass
                    if probs:
                        confidence_score = round((sum(probs) / len(probs)) * 100, 1)

            return PageResult(
                page_num=page_num,
                response=page_response,
                input_tokens=data.get("usage", {}).get("prompt_tokens", 0),
                output_tokens=data.get("usage", {}).get("completion_tokens", 0),
                is_fallback=False,
                success=True,
                confidence_score=confidence_score,
            )

        except asyncio.CancelledError:
            raise
        except httpx.ConnectError as e:
            logger.error("Connection error on page %d attempt %d: %s", page_num, attempt, e)
            if attempt < max_retries:
                await asyncio.sleep(5 * (2 ** attempt))
            continue
        except httpx.TimeoutException as e:
            logger.warning("Timeout on page %d attempt %d: %s", page_num, attempt, e)
            if attempt < max_retries:
                await asyncio.sleep(2 * (2 ** attempt))
            continue
        except Exception as e:
            logger.warning("Error on page %d attempt %d: %s: %s", page_num, attempt, type(e).__name__, e)
            continue

    # All retries exhausted — return a fallback result
    logger.error("All %d attempts failed for page %d", max_retries + 1, page_num)
    return PageResult(
        page_num=page_num,
        response=PageResponse(
            primary_language=None,
            is_rotation_valid=True,
            rotation_correction=0,
            is_table=False,
            is_diagram=False,
            natural_text=None,
        ),
        is_fallback=True,
        success=False,
        error_message=f"Failed after {max_retries + 1} attempts",
    )


def parse_page_range(range_str: str, max_pages: int) -> list[int]:
    """
    Parse a page range string (e.g. "1-5, 8, 11-13") and return a sorted list of 1-indexed integers.
    If the string is empty or completely malformed, returns list(range(1, max_pages + 1)).
    """
    if not range_str or not isinstance(range_str, str) or not range_str.strip():
        return list(range(1, max_pages + 1))

    pages = set()
    parts = range_str.split(",")
    for part in parts:
        part = part.strip()
        if not part:
            continue
        if "-" in part:
            try:
                sub_parts = [p.strip() for p in part.split("-")]
                if len(sub_parts) == 2:
                    start = int(sub_parts[0])
                    end = int(sub_parts[1])
                    if start > end:
                        start, end = end, start
                    for i in range(start, end + 1):
                        if 1 <= i <= max_pages:
                            pages.add(i)
                else:
                    logger.warning("Ignoring invalid range part: %s", part)
            except ValueError:
                logger.warning("Failed parsing range bounds for part: %s", part)
        else:
            try:
                val = int(part)
                if 1 <= val <= max_pages:
                    pages.add(val)
                else:
                    logger.warning("Page number %d out of bounds (1-%d)", val, max_pages)
            except ValueError:
                logger.warning("Ignoring invalid non-integer part: %s", part)

    if not pages:
        logger.warning("No valid pages resolved from range '%s', falling back to all pages", range_str)
        return list(range(1, max_pages + 1))

    return sorted(list(pages))


# ---------------------------------------------------------------------------
# Full PDF processing pipeline
# ---------------------------------------------------------------------------

async def process_pdf_to_markdown(
    pdf_path: str,
    settings: dict,
    progress_callback: Optional[ProgressCallback] = None,
) -> dict:
    """
    Main entry point: process an entire PDF into a single merged Markdown file.

    Args:
        pdf_path: Absolute path to the input PDF file.
        settings: Dict of processing settings (server_url, model, workers, etc.).
        progress_callback: Optional async callback for real-time progress updates.
            Signature: async callback(page_num, total_pages, event, message)

    Returns:
        Dict with keys: output_path, total_pages, pages_completed, pages_failed, duration_seconds
    """
    start_time = time.time()

    # Read PDF metadata
    reader = PdfReader(pdf_path)
    pdf_total_pages = len(reader.pages)
    pdf_name = Path(pdf_path).stem

    # Settings
    server_url = settings.get("server_url", "http://192.168.20.50:1234/v1")
    model = settings.get("model", "allenai_olmocr-2-7b-1025")
    workers = settings.get("workers", 2)
    target_dim = settings.get("target_longest_image_dim", 768)
    max_tokens = settings.get("max_tokens", 8000)
    max_retries = settings.get("max_page_retries", 1)
    page_range_str = settings.get("page_range", "")

    # Parse targeted pages
    target_pages = parse_page_range(page_range_str, pdf_total_pages)
    target_count = len(target_pages)

    # Output paths
    from backend.config import get_output_dir
    output_dir = get_output_dir()
    temp_pages_dir = output_dir / f".{pdf_name}_pages"
    temp_pages_dir.mkdir(parents=True, exist_ok=True)
    output_file = output_dir / f"{pdf_name}_FULL.md"

    logger.info("Starting OCR: %s (%d pages selected, %d workers)", pdf_path, target_count, workers)

    if progress_callback:
        await progress_callback(0, target_count, "job_start", f"Processing {pdf_name} ({target_count} pages)", None, None)

    # Semaphore to limit concurrent requests
    semaphore = asyncio.Semaphore(workers)
    results: list[PageResult] = [None] * pdf_total_pages  # type: ignore
    pages_completed = 0
    pages_failed = 0
    page_times: list[float] = []

    async def process_with_semaphore(page_num: int):
        nonlocal pages_completed, pages_failed

        async with semaphore:
            if progress_callback:
                eta = None
                done_current = pages_completed + pages_failed
                if page_times:
                    avg_time = sum(page_times) / len(page_times)
                    remaining = target_count - done_current
                    eta = avg_time * remaining / max(workers, 1)
                await progress_callback(page_num, target_count, "page_start", f"Processing page {page_num}", eta, None)

            page_start = time.time()
            page_file = temp_pages_dir / f"page_{page_num:03d}.md"

            if page_file.exists() and page_file.stat().st_size > 10:
                logger.info("Resuming page %d from cache: %s", page_num, page_file.name)
                text = page_file.read_text(encoding="utf-8")
                result = PageResult(
                    page_num=page_num,
                    response=PageResponse(
                        primary_language=None,
                        is_rotation_valid=True,
                        rotation_correction=0,
                        is_table=False,
                        is_diagram=False,
                        natural_text=text
                    ),
                    success=True
                )
            else:
                async with httpx.AsyncClient() as client:
                    result = await process_single_page(
                        client, pdf_path, page_num,
                        server_url, model, target_dim, max_tokens, max_retries
                    )

                # Save page result incrementally (crash recovery)
                text = result.response.natural_text or ""
                page_file.write_text(text, encoding="utf-8")

            page_elapsed = time.time() - page_start
            page_times.append(page_elapsed)
            results[page_num - 1] = result

            if result.success:
                pages_completed += 1
                event = "page_complete"
                msg = f"Page {page_num}/{target_count} complete ({page_elapsed:.1f}s)"
            else:
                pages_failed += 1
                event = "page_failed"
                msg = f"Page {page_num}/{target_count} failed: {result.error_message}"

            done = pages_completed + pages_failed
            if progress_callback:
                # Estimate ETA
                eta = None
                if page_times:
                    avg_time = sum(page_times) / len(page_times)
                    remaining = target_count - done
                    eta = avg_time * remaining / max(workers, 1)

                await progress_callback(
                    page_num, target_count, event,
                    msg, eta, result.confidence_score
                )

            logger.info(msg)

    # Process all pages concurrently (limited by semaphore)
    tasks = [process_with_semaphore(p) for p in target_pages]
    await asyncio.gather(*tasks)

    # Merge all pages into a single Markdown file
    logger.info("Merging %d pages into %s", target_count, output_file)
    with open(output_file, "w", encoding="utf-8") as outfile:
        for i in target_pages:
            result = results[i - 1]
            if result and result.success and result.response.natural_text:
                outfile.write(f"<!-- PAGE {i:03d} -->\n")
                outfile.write(result.response.natural_text)
                outfile.write("\n\n")
            else:
                outfile.write(f"<!-- PAGE {i:03d} FAILED OR EMPTY -->\n\n")

    duration = time.time() - start_time

    # Clean up temp pages directory
    try:
        import shutil
        shutil.rmtree(temp_pages_dir, ignore_errors=True)
    except Exception:
        pass

    logger.info(
        "OCR complete: %s — %d/%d pages successful, %d failed (%.1fs)",
        pdf_name, pages_completed, target_count, pages_failed, duration
    )

    if progress_callback:
        await progress_callback(
            target_count, target_count, "job_complete",
            f"Complete: {pages_completed}/{target_count} pages ({duration:.1f}s)", None, None
        )

    return {
        "output_path": str(output_file),
        "output_filename": output_file.name,
        "total_pages": target_count,
        "pages_completed": pages_completed,
        "pages_failed": pages_failed,
        "duration_seconds": round(duration, 1),
    }
