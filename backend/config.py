"""
OCR Studio — Application Configuration

Manages default settings, user overrides (via settings.json),
and directory paths for the OCR Studio application.
"""

import json
import logging
from pathlib import Path

logger = logging.getLogger("ocr_studio.config")

# Project root directory (D:\OCR_PROJECTS)
PROJECT_ROOT = Path(__file__).resolve().parent.parent

# Directory paths
LOGS_DIR = PROJECT_ROOT / "logs"
SETTINGS_FILE = PROJECT_ROOT / "settings.json"

# Default application settings
DEFAULTS = {
    "server_url": "http://192.168.20.50:1234/v1",
    "model": "allenai_olmocr-2-7b-1025",
    "translation_model": "",
    "translation_profile": "universal",
    "workers": 2,
    "pages_per_group": 5,
    "target_longest_image_dim": 1288,
    "max_page_retries": 4,
    "max_tokens": 8000,
    "output_dir": "",
    "page_range": "",
    "custom_glossary": "",
    "strict_mode": False,
    "reading_direction": "Default",
    "document_structure": "Standard",
    "binarize": False,
    "high_contrast": False,
    "despeckle": False,
    "consensus_mode": False,
}


def load_settings() -> dict:
    """
    Load user settings from settings.json, merged with defaults.
    Missing keys are filled in from DEFAULTS.
    """
    settings = dict(DEFAULTS)

    if SETTINGS_FILE.exists():
        try:
            with open(SETTINGS_FILE, "r", encoding="utf-8") as f:
                user_settings = json.load(f)
            settings.update(user_settings)
            logger.info("Loaded user settings from %s", SETTINGS_FILE)
        except (json.JSONDecodeError, IOError) as e:
            logger.warning("Failed to read settings.json, using defaults: %s", e)

    return settings


def save_settings(settings: dict):
    """
    Save user settings to settings.json.
    Only saves keys that differ from defaults or are user-configurable.
    """
    try:
        with open(SETTINGS_FILE, "w", encoding="utf-8") as f:
            json.dump(settings, f, indent=2, ensure_ascii=False)
        logger.info("Saved user settings to %s", SETTINGS_FILE)
    except IOError as e:
        logger.error("Failed to write settings.json: %s", e)
        raise


def get_output_dir() -> Path:
    """Get the active output directory from settings, defaulting to PROJECT_ROOT/output."""
    settings = load_settings()
    out_dir = settings.get("output_dir", "").strip()
    if out_dir:
        return Path(out_dir).resolve()
    return PROJECT_ROOT / "output"


def get_upload_dir() -> Path:
    """Get the active upload directory inside the output directory."""
    return get_output_dir() / "uploads"


def resolve_within_base(base: Path, user_filename: str) -> Path:
    """
    Safely resolve a user-supplied filename inside a trusted base directory.

    Guards against path traversal (``..\\``, ``../`` and their URL-decoded
    variants) and absolute-path injection (e.g. ``C:\\Windows\\...``). Returns
    the resolved absolute Path only if it is contained within ``base``;
    otherwise raises ``ValueError``.
    """
    if not user_filename or not str(user_filename).strip():
        raise ValueError("Empty filename")

    base_resolved = base.resolve()
    candidate = (base_resolved / user_filename).resolve()

    if candidate != base_resolved and base_resolved not in candidate.parents:
        raise ValueError(f"Path escapes base directory: {user_filename!r}")

    return candidate


# ---------------------------------------------------------------------------
# Glossary presets (shippable domain glossaries under glossaries/)
# ---------------------------------------------------------------------------

def get_glossaries_dir() -> Path:
    """Directory holding shippable glossary preset files (root-level 'glossaries/')."""
    return PROJECT_ROOT / "glossaries"


def list_glossaries() -> list:
    """Return sorted stem names of available *.txt glossary presets."""
    gdir = get_glossaries_dir()
    if not gdir.exists():
        return []
    return sorted(p.stem for p in gdir.glob("*.txt"))


def load_glossary_terms(name: str) -> str:
    """
    Load a glossary preset by stem name (without .txt) and return its terms as a
    comma-joined string ready for prompt injection.

    Lines are '#'-comment aware: the term is the text before any inline '#';
    blank lines and comment-only lines are ignored. Raises ValueError on an
    invalid/escaping name or a missing file.
    """
    path = resolve_within_base(get_glossaries_dir(), f"{name}.txt")
    if not path.exists() or not path.is_file():
        raise ValueError(f"Glossary not found: {name!r}")

    terms = []
    for line in path.read_text(encoding="utf-8").splitlines():
        term = line.split("#", 1)[0].strip()
        if term:
            terms.append(term)
    return ", ".join(terms)


def ensure_directories():
    """Create required project directories if they don't exist."""
    get_output_dir().mkdir(parents=True, exist_ok=True)
    LOGS_DIR.mkdir(parents=True, exist_ok=True)
    get_upload_dir().mkdir(parents=True, exist_ok=True)


# Guarantee directories exist immediately upon importing configuration
ensure_directories()
