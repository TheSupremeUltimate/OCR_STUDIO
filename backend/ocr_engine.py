"""
OCR Studio — Core OCR Engine

Processes a PDF file page-by-page through a vision language model (LM Studio)
and produces a merged Markdown output file.

Adapted from OlmOCR (Apache 2.0 License) — https://github.com/allenai/olmocr
Key functions adapted: render_pdf_to_base64png, build_page_query, response parsing.
"""

import asyncio
import base64
import difflib
import json
import logging
import math
import os
import subprocess
import time
from dataclasses import dataclass
from io import BytesIO
from pathlib import Path
from typing import Any, Callable, Coroutine, Dict, List, Optional

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
    attempts_taken: int = 1
    token_logprobs: Optional[List[Dict[str, Any]]] = None


# Progress callback type
ProgressCallback = Callable[[int, int, str, Optional[str], Optional[float], Optional[float], Optional[List[Dict[str, Any]]]], Coroutine[Any, Any, None]]
# Signature: async callback(page_num, total_pages, event, message, eta, confidence, token_logprobs)


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


def render_pdf_to_base64png(
    local_pdf_path: str,
    page_num: int,
    target_longest_image_dim: int = 768,
    binarize: bool = False,
    high_contrast: bool = False,
    despeckle: bool = False,
) -> str:
    """Render a single PDF page to a base64-encoded PNG string using pdftoppm, with optional filters."""
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

    # Apply Image Pre-processing filters if any are enabled
    if binarize or high_contrast or despeckle:
        from PIL import Image, ImageEnhance, ImageFilter
        from io import BytesIO

        # Load raw pdftoppm output bytes into a Pillow Image
        img = Image.open(BytesIO(pdftoppm_result.stdout))

        if despeckle:
            img = img.filter(ImageFilter.MedianFilter(size=3))

        if high_contrast:
            # Convert to grayscale first for crisp contrast enhancement
            img = ImageEnhance.Contrast(img.convert("L")).enhance(2.0)

        if binarize:
            # Global binary thresholding (converting pixel values < 127 to black, others to white)
            img = img.convert("L").point(lambda x: 0 if x < 127 else 255, mode="1")

        buffered = BytesIO()
        img.save(buffered, format="PNG")
        return base64.b64encode(buffered.getvalue()).decode("utf-8")

    return base64.b64encode(pdftoppm_result.stdout).decode("utf-8")



# ---------------------------------------------------------------------------
# Prompt building (adapted from OlmOCR prompts.py)
# ---------------------------------------------------------------------------

def build_prompt(
    custom_glossary: str = "",
    strict_mode: bool = False,
    reading_direction: str = "Default",
    previous_page_context: str = "",
    document_structure: str = "Standard",
) -> str:
    """Build the vision model prompt (OlmOCR v4 no-anchoring YAML prompt)."""
    prompt = (
        "Attached is one page of a document that you must process. "
        "Just return the plain text representation of this document as if you were reading it naturally. "
        "Convert equations to LateX and tables to HTML.\n"
        "If there are any figures or charts, label them with the following markdown syntax "
        "![Alt text describing the contents of the figure](page_startx_starty_width_height.png)\n"
        "Return your output as markdown, with a front matter section on top specifying values for the "
        "primary_language, is_rotation_valid, rotation_correction, is_table, and is_diagram parameters."
    )

    appends = []
    if previous_page_context and previous_page_context.strip():
        appends.append(
            f"For context, the previous page ended with the following trailing text: [ {previous_page_context.strip()} ]. "
            f"Please continue transcribing the attached page image, ensuring seamless sentence flow. "
            f"Do not repeat the context text in your response."
        )
    if custom_glossary and custom_glossary.strip():
        appends.append(f"This text contains document-specific terms/proper nouns. Prioritize matching these sequences visually: {custom_glossary.strip()}")
    if strict_mode:
        appends.append("Do not modernize characters, do not correct perceived historical typos, and do not fill in gaps. Provide an exact 1:1 digital twin of the glyphs present.")
    if reading_direction == "Vertical RTL":
        appends.append(
            "This is a traditional Chinese document read vertically from right to left. "
            "Process the vertical columns strictly from right to left, and read each column from top to bottom. "
            "Do not arbitrarily break lines. "
            "CRITICAL: Do not skip the text printed in the outer margins of the page, even if it is located OUTSIDE the double-line vertical border frame. "
            "You must start transcribing from the very first column on the far-right outer margin outside the border "
            "and proceed column by column to the left, transcribing everything inside and outside the borders. Do not omit any marginal comments."
        )
    elif reading_direction == "Horizontal LTR":
        appends.append("This text should be read horizontally from left to right, top to bottom.")
    if document_structure == "Main Text + Interline Commentary":
        appends.append("This document features large main text and small interline commentary (often printed in double columns). Do not arbitrarily break lines. Transcribe the main text continuously. When you encounter small commentary text, wrap it entirely in [brackets] and place it immediately after the preceding main text character it annotates.")

    if appends:
        prompt += "\n" + "\n".join(appends)

    return prompt


async def build_page_query(
    local_pdf_path: str,
    page: int,
    target_longest_image_dim: int,
    model_name: str,
    max_tokens: int = 1600,
    temperature: float = 0.1,
    custom_glossary: str = "",
    strict_mode: bool = False,
    reading_direction: str = "Default",
    previous_page_context: str = "",
    override_image_base64: Optional[str] = None,
    document_structure: str = "Standard",
    binarize: bool = False,
    high_contrast: bool = False,
    despeckle: bool = False,
) -> dict:
    """Construct the OpenAI-compatible chat completion request for a single page."""

    if override_image_base64:
        image_base64 = override_image_base64
    else:
        # Render in a thread to avoid blocking the event loop
        image_base64 = await asyncio.to_thread(
            render_pdf_to_base64png, local_pdf_path, page, target_longest_image_dim,
            binarize, high_contrast, despeckle
        )


    return {
        "model": model_name,
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": build_prompt(custom_glossary, strict_mode, reading_direction, previous_page_context, document_structure)},
                    {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{image_base64}"}},
                ],
            }
        ],
        "max_tokens": max_tokens,
        "temperature": temperature,
        "logprobs": True,
        "top_logprobs": 5,
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
    custom_glossary: str = "",
    strict_mode: bool = False,
    reading_direction: str = "Default",
    previous_page_context: str = "",
    document_structure: str = "Standard",
    binarize: bool = False,
    high_contrast: bool = False,
    despeckle: bool = False,
    override_image_base64: Optional[str] = None,
    skip_consensus_check: bool = False,
) -> PageResult:
    """
    Process a single PDF page: render → send to model → parse response.
    Includes retry logic with escalating temperature.
    """
    completion_url = f"{server_url.rstrip('/')}/chat/completions"
    current_target_dim = target_dim
    controlled_retries = 0

    for attempt in range(max_retries + 1):
        effective_temp_idx = max(0, attempt - controlled_retries)
        temp_idx = min(effective_temp_idx, len(TEMPERATURE_BY_ATTEMPT) - 1)
        temperature = TEMPERATURE_BY_ATTEMPT[temp_idx]

        try:
            query = await build_page_query(
                pdf_path, page_num, current_target_dim, model, max_tokens, temperature,
                custom_glossary, strict_mode, reading_direction, previous_page_context,
                override_image_base64=override_image_base64,
                document_structure=document_structure,
                binarize=binarize,
                high_contrast=high_contrast,
                despeckle=despeckle
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
            finish_reason = data["choices"][0].get("finish_reason")
            if finish_reason != "stop":
                logger.warning("Incomplete response for page %d attempt %d (reason: %s)", page_num, attempt, finish_reason)
                if finish_reason == "length":
                    max_tokens = min(max_tokens + 1000, 4096)
                    controlled_retries += 1
                    logger.info("Page %d hit token limit. Increasing max_tokens to %d for next attempt.", page_num, max_tokens)
                continue

            model_text = data["choices"][0]["message"]["content"]
            page_response = parse_model_response(model_text)

            # Calculate confidence score and token logprobs if available
            confidence_score = None
            token_logprobs = None
            choice = data["choices"][0]
            if "logprobs" in choice and choice["logprobs"] is not None:
                logprobs_data = choice["logprobs"]
                content_logprobs = logprobs_data.get("content")
                if content_logprobs:
                    probs = []
                    # Reconstruct the full text to align tokens to natural_text
                    full_text_reconstructed = ""
                    token_starts = []
                    for t in content_logprobs:
                        token_str = t.get("token", "")
                        token_starts.append(len(full_text_reconstructed))
                        full_text_reconstructed += token_str
                        lp = t.get("logprob")
                        if lp is not None:
                            try:
                                probs.append(math.exp(float(lp)))
                            except (ValueError, OverflowError):
                                pass
                    if probs:
                        confidence_score = round((sum(probs) / len(probs)) * 100, 1)

                    # Align token index range to the start of natural_text
                    natural_start_idx = 0
                    if full_text_reconstructed.startswith("---\n"):
                        fm_end = full_text_reconstructed.find("\n---", 4)
                        if fm_end != -1:
                            fm_close = fm_end + 4
                            while fm_close < len(full_text_reconstructed) and full_text_reconstructed[fm_close].isspace():
                                fm_close += 1
                            natural_start_idx = fm_close
                    else:
                        if page_response.natural_text:
                            idx = full_text_reconstructed.find(page_response.natural_text)
                            if idx != -1:
                                natural_start_idx = idx

                    # Filter and structure token logprobs for natural_text
                    token_logprobs = []
                    for idx, t in enumerate(content_logprobs):
                        start_pos = token_starts[idx]
                        if start_pos >= natural_start_idx:
                            lp = t.get("logprob")
                            prob = round(math.exp(float(lp)) * 100, 1) if lp is not None else None
                            
                            top_lps = []
                            if "top_logprobs" in t and t["top_logprobs"]:
                                for top_t in t["top_logprobs"]:
                                    top_lp = top_t.get("logprob")
                                    top_prob = round(math.exp(float(top_lp)) * 100, 1) if top_lp is not None else None
                                    top_lps.append({
                                        "token": top_t.get("token", ""),
                                        "logprob": top_lp,
                                        "confidence": top_prob
                                    })
                            token_logprobs.append({
                                "token": t.get("token", ""),
                                "logprob": lp,
                                "confidence": prob,
                                "top_logprobs": top_lps
                            })

            # --- DYNAMIC SELF-CORRECTION & SMART RETRIES ---

            # 1. Auto-Rotate check (Bypassed for Vertical RTL to preserve geometry hack)
            if not page_response.is_rotation_valid and reading_direction != "Vertical RTL" and attempt < max_retries:
                logger.info(
                    "Page %d: Invalid rotation detected. Attempting 90-degree rotation correction.", 
                    page_num
                )
                try:
                    sent_image_url = query["messages"][0]["content"][1]["image_url"]["url"]
                    sent_base64 = sent_image_url.split("base64,")[1]
                    image_bytes = base64.b64decode(sent_base64)
                    image = Image.open(BytesIO(image_bytes))
                    
                    angle = -90
                    if page_response.rotation_correction in [90, 180, 270]:
                        angle = -page_response.rotation_correction
                    
                    rotated_image = image.rotate(angle, expand=True)
                    buffered = BytesIO()
                    rotated_image.save(buffered, format="PNG")
                    override_image_base64 = base64.b64encode(buffered.getvalue()).decode("utf-8")
                    
                    controlled_retries += 1
                    logger.info("Page %d rotated. Triggering retry attempt %d...", page_num, attempt + 1)
                    continue
                except Exception as e:
                    logger.warning("Failed to rotate image for page %d: %s", page_num, e)

            # 2. Auto-Upscale check (if it's a table)
            if page_response.is_table and current_target_dim < 2048 and attempt < max_retries:
                logger.info("Page %d: Table detected. Auto-upscaling to 2048px for next attempt.", page_num)
                current_target_dim = 2048
                override_image_base64 = None  # Force re-render at new resolution
                controlled_retries += 1
                continue

            # 3. Smart retry check for low confidence
            if confidence_score is not None and confidence_score < 75.0:
                if attempt < max_retries:
                    new_dim = min(int(current_target_dim * 1.25), 3072)
                    if new_dim > current_target_dim:
                        logger.info(
                            "Page %d: Low confidence (%.1f%%). Retrying with upscaled dimension %dpx.",
                            page_num, confidence_score, new_dim
                        )
                        current_target_dim = new_dim
                        override_image_base64 = None  # Force re-render at new resolution
                        controlled_retries += 1
                        continue
                elif not skip_consensus_check:
                    logger.info("Page %d: Final attempt confidence (%.1f%%) is below 75%%. Triggering Adaptive Density Chunking...", page_num, confidence_score)
                    try:
                        chunk_result = await run_adaptive_density_chunking(
                            http_client, pdf_path, page_num, server_url, model, current_target_dim, max_tokens, max_retries,
                            custom_glossary, strict_mode, reading_direction, previous_page_context, document_structure,
                            binarize, high_contrast, despeckle
                        )
                        return chunk_result
                    except Exception as e:
                        logger.error("Adaptive Density Chunking failed for page %d: %s", page_num, e)

            return PageResult(
                page_num=page_num,
                response=page_response,
                input_tokens=data.get("usage", {}).get("prompt_tokens", 0),
                output_tokens=data.get("usage", {}).get("completion_tokens", 0),
                is_fallback=False,
                success=True,
                confidence_score=confidence_score,
                attempts_taken=attempt + 1,
                token_logprobs=token_logprobs,
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
        attempts_taken=max_retries + 1,
        token_logprobs=None,
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

def generate_toc(markdown_text: str) -> str:
    """Scan markdown for H2 headers (## Header Title) and generate a Table of Contents."""
    import re
    lines = markdown_text.splitlines()
    toc_links = []
    
    for line in lines:
        if line.startswith("## ") or line.startswith("##\t"):
            title = line[3:].strip()
            if title:
                # slugify: lowercase, replace spaces with -, remove special chars (retaining unicode words)
                slug = title.lower()
                slug = re.sub(r"[^\w\s-]", "", slug)
                slug = re.sub(r"\s+", "-", slug)
                toc_links.append(f"- [{title}](#{slug})")
                
    if not toc_links:
        return markdown_text
        
    toc_block = "# Table of Contents\n\n" + "\n".join(toc_links) + "\n\n---\n\n"
    return toc_block + markdown_text


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
    custom_glossary = settings.get("custom_glossary", "")
    strict_mode = settings.get("strict_mode", False)
    reading_direction = settings.get("reading_direction", "Default")
    document_structure = settings.get("document_structure", "Standard")
    binarize = settings.get("binarize", False)
    high_contrast = settings.get("high_contrast", False)
    despeckle = settings.get("despeckle", False)

    # Auto-upscale to 2400px for Vertical RTL documents to ensure tiny marginalia characters are legible to the VLM
    if reading_direction == "Vertical RTL" and target_dim < 2400:
        target_dim = 2400

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
        await progress_callback(0, target_count, "job_start", f"Processing {pdf_name} ({target_count} pages)", None, None, None)

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
                await progress_callback(page_num, target_count, "page_start", f"Processing page {page_num}", eta, None, None)

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
                # Retrieve trailing context from the previous page if available
                previous_page_context = ""
                if page_num > 1:
                    # Try reading from in-memory results list first
                    prev_res = results[page_num - 2]
                    if prev_res and prev_res.success and prev_res.response.natural_text:
                        prev_text = prev_res.response.natural_text.strip()
                        previous_page_context = prev_text[-200:]
                    else:
                        # Fallback to disk cache (e.g. if resumed or finished by another worker task)
                        prev_file = temp_pages_dir / f"page_{(page_num - 1):03d}.md"
                        if prev_file.exists():
                            try:
                                prev_text = prev_file.read_text(encoding="utf-8").strip()
                                previous_page_context = prev_text[-200:]
                            except Exception as e:
                                logger.warning("Failed to read context from previous page file: %s", e)

                async with httpx.AsyncClient() as client:
                    if settings.get("consensus_mode", False):
                        result = await run_consensus_ocr(
                            client, pdf_path, page_num,
                            server_url, model, max_tokens, max_retries,
                            custom_glossary, strict_mode, reading_direction,
                            previous_page_context=previous_page_context,
                            document_structure=document_structure,
                            binarize=binarize,
                            high_contrast=high_contrast,
                            despeckle=despeckle
                        )
                    else:
                        result = await process_single_page(
                            client, pdf_path, page_num,
                            server_url, model, target_dim, max_tokens, max_retries,
                            custom_glossary, strict_mode, reading_direction,
                            previous_page_context=previous_page_context,
                            document_structure=document_structure,
                            binarize=binarize,
                            high_contrast=high_contrast,
                            despeckle=despeckle
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
                    msg, eta, result.confidence_score, result.token_logprobs
                )

            logger.info(msg)

    # Process all pages concurrently (limited by semaphore)
    tasks = [process_with_semaphore(p) for p in target_pages]
    await asyncio.gather(*tasks)

    # Merge all pages into a single Markdown file in memory
    logger.info("Merging %d pages in memory", target_count)
    merged_lines = []
    for i in target_pages:
        result = results[i - 1]
        if result and result.success and result.response.natural_text:
            merged_lines.append(f"<!-- PAGE {i:03d} -->\n{result.response.natural_text}\n\n")
        else:
            merged_lines.append(f"<!-- PAGE {i:03d} FAILED OR EMPTY -->\n\n")
            
    merged_text = "".join(merged_lines)
    
    # Prepend Table of Contents if H2 headers are found
    merged_text = generate_toc(merged_text)
    
    # Write to final merged file
    logger.info("Writing merged pages to %s", output_file)
    with open(output_file, "w", encoding="utf-8") as outfile:
        outfile.write(merged_text)

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

    # Compute aggregates for job return
    valid_results = [r for r in results if r is not None]
    confidences = [r.confidence_score for r in valid_results if r.success and r.confidence_score is not None]
    avg_conf = round(sum(confidences) / len(confidences), 1) if confidences else None
    
    total_retries = sum((r.attempts_taken - 1) for r in valid_results if hasattr(r, "attempts_taken"))

    if progress_callback:
        await progress_callback(
            target_count, target_count, "job_complete",
            f"Complete: {pages_completed}/{target_count} pages ({duration:.1f}s)", None, avg_conf, None
        )

    return {
        "output_path": str(output_file),
        "output_filename": output_file.name,
        "total_pages": target_count,
        "pages_completed": pages_completed,
        "pages_failed": pages_failed,
        "duration_seconds": round(duration, 1),
        "average_confidence": avg_conf,
        "total_retries": total_retries,
    }


def crop_and_filter_pdf_page(
    local_pdf_path: str,
    page_num: int,
    x: float,
    y: float,
    width: float,
    height: float,
    target_longest_image_dim: int = 768,
    binarize: bool = False,
    high_contrast: bool = False,
    despeckle: bool = False,
) -> str:
    """Render a page, crop to normalized coordinates, apply filters, and return base64 PNG."""
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

    from PIL import Image, ImageEnhance, ImageFilter
    from io import BytesIO

    img = Image.open(BytesIO(pdftoppm_result.stdout))
    img_w, img_h = img.size

    # Calculate actual pixel coordinates from normalized values
    left = max(0, min(int(x * img_w), img_w - 1))
    top = max(0, min(int(y * img_h), img_h - 1))
    right = max(left + 1, min(int((x + width) * img_w), img_w))
    bottom = max(top + 1, min(int((y + height) * img_h), img_h))

    # Crop the image
    cropped_img = img.crop((left, top, right, bottom))

    # Apply filters
    if despeckle:
        cropped_img = cropped_img.filter(ImageFilter.MedianFilter(size=3))

    if high_contrast:
        cropped_img = ImageEnhance.Contrast(cropped_img.convert("L")).enhance(2.0)

    if binarize:
        cropped_img = cropped_img.convert("L").point(lambda val: 0 if val < 127 else 255, mode="1")

    buffered = BytesIO()
    cropped_img.save(buffered, format="PNG")
    return base64.b64encode(buffered.getvalue()).decode("utf-8")


async def process_cropped_zone(
    pdf_path: str,
    page_num: int,
    x: float,
    y: float,
    width: float,
    height: float,
    settings: dict,
) -> PageResult:
    """
    OCR a specific cropped area of a page.
    Calls build_page_query with the cropped image's base64 representation.
    """
    server_url = settings.get("server_url", "http://192.168.20.50:1234/v1")
    model = settings.get("model", "allenai_olmocr-2-7b-1025")
    target_dim = settings.get("target_longest_image_dim", 768)
    max_tokens = settings.get("max_tokens", 8000)
    max_retries = settings.get("max_page_retries", 1)
    custom_glossary = settings.get("custom_glossary", "")
    strict_mode = settings.get("strict_mode", False)
    reading_direction = settings.get("reading_direction", "Default")
    document_structure = settings.get("document_structure", "Standard")
    binarize = settings.get("binarize", False)
    high_contrast = settings.get("high_contrast", False)
    despeckle = settings.get("despeckle", False)

    # Auto-upscale to 2400px for Vertical RTL documents to ensure tiny marginalia characters are legible to the VLM
    if reading_direction == "Vertical RTL" and target_dim < 2400:
        target_dim = 2400

    # Render crop in a thread to keep async event loop free
    cropped_base64 = await asyncio.to_thread(
        crop_and_filter_pdf_page,
        pdf_path, page_num, x, y, width, height,
        target_dim, binarize, high_contrast, despeckle
    )

    async with httpx.AsyncClient() as client:
        result = await process_single_page(
            client, pdf_path, page_num,
            server_url, model, target_dim, max_tokens, max_retries,
            custom_glossary, strict_mode, reading_direction,
            previous_page_context="",
            document_structure=document_structure,
            binarize=binarize,
            high_contrast=high_contrast,
            despeckle=despeckle,
            override_image_base64=cropped_base64
        )
    return result


def align_strings(ref: str, s: str) -> tuple[str, str]:
    """Align string s to reference ref using gap padding '-'."""
    matcher = difflib.SequenceMatcher(None, ref, s)
    ref_aligned = []
    s_aligned = []
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == 'equal':
            ref_aligned.append(ref[i1:i2])
            s_aligned.append(s[j1:j2])
        elif tag == 'delete':
            ref_aligned.append(ref[i1:i2])
            s_aligned.append('-' * (i2 - i1))
        elif tag == 'insert':
            ref_aligned.append('-' * (j2 - j1))
            s_aligned.append(s[j1:j2])
        elif tag == 'replace':
            ref_len = i2 - i1
            s_len = j2 - j1
            if ref_len == s_len:
                ref_aligned.append(ref[i1:i2])
                s_aligned.append(s[j1:j2])
            else:
                ref_aligned.append(ref[i1:i2] + '-' * max(0, s_len - ref_len))
                s_aligned.append(s[j1:j2] + '-' * max(0, ref_len - s_len))
    return "".join(ref_aligned), "".join(s_aligned)


def vote_consensus(s1: str, s2: str, s3: str) -> str:
    """Perform Levenshtein-based 3-way alignment and vote character by character."""
    if s1 == s2 or s1 == s3:
        return s1
    if s2 == s3:
        return s2

    # Step 1: Align s2 to s1
    s1_a, s2_a = align_strings(s1, s2)
    # Step 2: Align s3 to the aligned s1 (s1_a) to capture s3 insertions
    s1_ab, s3_a = align_strings(s1_a, s3)
    # Step 3: Propagate s3's insertions back into s2_a
    _, s2_ab = align_strings(s1_a, s2_a)
    
    # Step 4: Character voting
    consensus_chars = []
    for c1, c2, c3 in zip(s1_ab, s2_ab, s3_a):
        votes = {}
        for c in (c1, c2, c3):
            if c != '-':
                votes[c] = votes.get(c, 0) + 1
        if votes:
            best_char = max(votes, key=votes.get)
            consensus_chars.append(best_char)
    return "".join(consensus_chars)


async def run_consensus_ocr(
    http_client: httpx.AsyncClient,
    pdf_path: str,
    page_num: int,
    server_url: str,
    model: str,
    max_tokens: int,
    max_retries: int,
    custom_glossary: str,
    strict_mode: bool,
    reading_direction: str,
    previous_page_context: str,
    document_structure: str,
    binarize: bool,
    high_contrast: bool,
    despeckle: bool,
) -> PageResult:
    """Run three concurrent OCR passes at different dimensions and vote consensus."""
    logger.info("Page %d: Starting Consensus Mode (768px, 1024px, 2048px concurrent passes)", page_num)
    
    tasks = [
        process_single_page(
            http_client, pdf_path, page_num, server_url, model,
            dim, max_tokens, max_retries, custom_glossary, strict_mode,
            reading_direction, previous_page_context, document_structure,
            binarize, high_contrast, despeckle, skip_consensus_check=True
        )
        for dim in [768, 1024, 2048]
    ]
    
    results = await asyncio.gather(*tasks)
    success_results = [r for r in results if r.success and r.response.natural_text]
    
    if not success_results:
        return results[0]
        
    if len(success_results) == 1:
        return success_results[0]
        
    if len(success_results) == 2:
        r1, r2 = success_results
        return r1 if (r1.confidence_score or 0) >= (r2.confidence_score or 0) else r2

    r1, r2, r3 = success_results
    text1 = r1.response.natural_text or ""
    text2 = r2.response.natural_text or ""
    text3 = r3.response.natural_text or ""
    
    consensus_text = vote_consensus(text1, text2, text3)
    avg_conf = round(sum(r.confidence_score or 0 for r in success_results) / 3, 1)
    
    base_result = r2  # 1024px pass as base metadata
    merged_response = PageResponse(
        primary_language=base_result.response.primary_language,
        is_rotation_valid=base_result.response.is_rotation_valid,
        rotation_correction=base_result.response.rotation_correction,
        is_table=base_result.response.is_table,
        is_diagram=base_result.response.is_diagram,
        natural_text=consensus_text
    )
    
    return PageResult(
        page_num=page_num,
        response=merged_response,
        input_tokens=sum(r.input_tokens for r in success_results),
        output_tokens=sum(r.output_tokens for r in success_results),
        is_fallback=False,
        success=True,
        confidence_score=avg_conf,
        attempts_taken=max(r.attempts_taken for r in success_results),
        token_logprobs=base_result.token_logprobs
    )


def crop_top_bottom_horizontal(
    local_pdf_path: str,
    page_num: int,
    target_longest_image_dim: int = 768,
    binarize: bool = False,
    high_contrast: bool = False,
    despeckle: bool = False,
) -> tuple[str, str]:
    """Render page and crop into top (0% to 60%) and bottom (40% to 100%) overlapping base64 PNGs."""
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

    from PIL import Image, ImageEnhance, ImageFilter
    from io import BytesIO

    img = Image.open(BytesIO(pdftoppm_result.stdout))
    img_w, img_h = img.size

    top_crop = img.crop((0, 0, img_w, int(img_h * 0.60)))
    bottom_crop = img.crop((0, int(img_h * 0.40), img_w, img_h))

    def apply_filters(crop_img):
        if despeckle:
            crop_img = crop_img.filter(ImageFilter.MedianFilter(size=3))
        if high_contrast:
            crop_img = ImageEnhance.Contrast(crop_img.convert("L")).enhance(2.0)
        if binarize:
            crop_img = crop_img.convert("L").point(lambda val: 0 if val < 127 else 255, mode="1")
        return crop_img

    top_crop = apply_filters(top_crop)
    bottom_crop = apply_filters(bottom_crop)

    buf_top = BytesIO()
    top_crop.save(buf_top, format="PNG")
    top_b64 = base64.b64encode(buf_top.getvalue()).decode("utf-8")

    buf_bottom = BytesIO()
    bottom_crop.save(buf_bottom, format="PNG")
    bottom_b64 = base64.b64encode(buf_bottom.getvalue()).decode("utf-8")

    return top_b64, bottom_b64


def merge_overlapping_texts(top_text: str, bottom_text: str) -> str:
    """Find the overlap seam between top_text and bottom_text and merge them."""
    if not top_text:
        return bottom_text
    if not bottom_text:
        return top_text

    top_text_clean = top_text.strip()
    bottom_text_clean = bottom_text.strip()

    # 1. Try exact suffix-prefix matching first (highest precision)
    max_overlap = min(len(top_text_clean), len(bottom_text_clean), 400)
    for i in range(max_overlap, 7, -1):
        if top_text_clean.endswith(bottom_text_clean[:i]):
            return top_text_clean + bottom_text_clean[i:]

    # 2. Try fuzzy matching with sliding window to handle OCR character noise
    for i in range(max_overlap, 15, -1):
        suffix = top_text_clean[-i:]
        prefix = bottom_text_clean[:i]
        ratio = difflib.SequenceMatcher(None, suffix, prefix).ratio()
        if ratio > 0.85:
            logger.info("Overlap stitching: found fuzzy overlap seam of length %d with ratio %.3f", i, ratio)
            return top_text_clean[:-i] + bottom_text_clean

    logger.warning("Overlap stitching: could not find overlapping seam, concatenating segments directly.")
    return top_text_clean + "\n\n" + bottom_text_clean


async def run_adaptive_density_chunking(
    http_client: httpx.AsyncClient,
    pdf_path: str,
    page_num: int,
    server_url: str,
    model: str,
    target_dim: int,
    max_tokens: int,
    max_retries: int,
    custom_glossary: str,
    strict_mode: bool,
    reading_direction: str,
    previous_page_context: str,
    document_structure: str,
    binarize: bool,
    high_contrast: bool,
    despeckle: bool,
) -> PageResult:
    """Slice page into top and bottom halves, OCR each segment, and stitch results."""
    logger.info("Page %d: Rendering top/bottom slices for Adaptive Density Chunking", page_num)
    top_b64, bottom_b64 = await asyncio.to_thread(
        crop_top_bottom_horizontal,
        pdf_path, page_num, target_dim, binarize, high_contrast, despeckle
    )

    top_result = await process_single_page(
        http_client, pdf_path, page_num, server_url, model, target_dim, max_tokens, max_retries,
        custom_glossary, strict_mode, reading_direction, previous_page_context, document_structure,
        binarize, high_contrast, despeckle,
        override_image_base64=top_b64,
        skip_consensus_check=True
    )

    prev_context = ""
    if top_result.success and top_result.response.natural_text:
        prev_context = top_result.response.natural_text.strip()[-200:]

    bottom_result = await process_single_page(
        http_client, pdf_path, page_num, server_url, model, target_dim, max_tokens, max_retries,
        custom_glossary, strict_mode, reading_direction, prev_context, document_structure,
        binarize, high_contrast, despeckle,
        override_image_base64=bottom_b64,
        skip_consensus_check=True
    )

    if not top_result.success:
        return top_result
    if not bottom_result.success:
        return bottom_result

    top_text = top_result.response.natural_text or ""
    bottom_text = bottom_result.response.natural_text or ""
    merged_text = merge_overlapping_texts(top_text, bottom_text)

    combined_logprobs = []
    if top_result.token_logprobs:
        combined_logprobs.extend(top_result.token_logprobs)
    if bottom_result.token_logprobs:
        combined_logprobs.extend(bottom_result.token_logprobs)

    avg_conf = round(((top_result.confidence_score or 0) + (bottom_result.confidence_score or 0)) / 2, 1)

    merged_response = PageResponse(
        primary_language=top_result.response.primary_language,
        is_rotation_valid=top_result.response.is_rotation_valid,
        rotation_correction=top_result.response.rotation_correction,
        is_table=top_result.response.is_table or bottom_result.response.is_table,
        is_diagram=top_result.response.is_diagram or bottom_result.response.is_diagram,
        natural_text=merged_text
    )

    return PageResult(
        page_num=page_num,
        response=merged_response,
        input_tokens=top_result.input_tokens + bottom_result.input_tokens,
        output_tokens=top_result.output_tokens + bottom_result.output_tokens,
        is_fallback=True,
        success=True,
        confidence_score=avg_conf,
        attempts_taken=top_result.attempts_taken + bottom_result.attempts_taken,
        token_logprobs=combined_logprobs
    )
