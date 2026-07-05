import sys
from pathlib import Path
import logging

# Ensure project root is in sys.path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

# Setup logging
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("verify_export")

from backend.export_utils import convert_markdown_to_html, convert_markdown_to_docx

def main():
    logger.info("Starting export verification tests...")
    
    # 1. Setup paths
    input_md_path = PROJECT_ROOT / "logs" / "test_input.md"
    output_html_path = PROJECT_ROOT / "logs" / "test_output.html"
    output_docx_path = PROJECT_ROOT / "logs" / "test_output.docx"
    
    # Ensure logs directory exists
    input_md_path.parent.mkdir(parents=True, exist_ok=True)
    
    # Clean up previous runs
    for path in [input_md_path, output_html_path, output_docx_path]:
        if path.exists():
            path.unlink()
            
    # Sample markdown text with single newlines, tables, code blocks, and PAGE comments
    sample_markdown = """# OCR Document Test
This is page one text.
This line is separated by a single newline.
This is another line with a single newline.

<!-- PAGE 1 -->

## Section on Page 2
Here is a table:

| Column A | Column B |
|---|---|
| Value 1 | Value 2 |

<!-- PAGE 2 -->

Here is some code:
```python
def hello():
    return "world"
```
"""
    
    # Write input MD
    input_md_path.write_text(sample_markdown, encoding="utf-8")
    logger.info(f"Wrote test markdown input to: {input_md_path}")
    
    # 2. Test HTML Conversion
    logger.info("Testing convert_markdown_to_html...")
    html_success = convert_markdown_to_html(input_md_path, output_html_path)
    if not html_success:
        logger.error("convert_markdown_to_html returned False")
        sys.exit(1)
        
    if not output_html_path.exists():
        logger.error("HTML output file was not created")
        sys.exit(1)
        
    html_content = output_html_path.read_text(encoding="utf-8")
    
    # Validate HTML requirements
    # nl2br should create line breaks
    if "<br" not in html_content:
        logger.error("FAIL: Single newlines were not converted to line breaks (<br>)")
        sys.exit(1)
    else:
        logger.info("PASS: Found <br> elements (nl2br extension verified).")
        
    # page-divider should be present
    if '<div class="html-page-divider">Page 1</div>' not in html_content:
        logger.error("FAIL: Page divider 1 not found in HTML output")
        sys.exit(1)
    if '<div class="html-page-divider">Page 2</div>' not in html_content:
        logger.error("FAIL: Page divider 2 not found in HTML output")
        sys.exit(1)
    logger.info("PASS: Page dividers successfully generated.")
    
    # Check tables
    if "<table>" not in html_content or "<td>Value 1</td>" not in html_content:
        logger.error("FAIL: Table was not parsed correctly")
        sys.exit(1)
    logger.info("PASS: Table successfully parsed.")
    
    # Check styles
    if ".html-page-divider" not in html_content or "page-break-before: always;" not in html_content:
        logger.error("FAIL: CSS styles for page divider are missing in HTML template")
        sys.exit(1)
    logger.info("PASS: CSS styling is correct.")
    
    # 3. Test DOCX Conversion
    logger.info("Testing convert_markdown_to_docx...")
    docx_success = convert_markdown_to_docx(input_md_path, output_docx_path)
    if not docx_success:
        logger.error("convert_markdown_to_docx returned False")
        sys.exit(1)
        
    if not output_docx_path.exists():
        logger.error("DOCX output file was not created")
        sys.exit(1)
        
    logger.info("PASS: DOCX file successfully created.")
    
    logger.info("All export utility verification tests PASSED successfully!")
    sys.exit(0)

if __name__ == "__main__":
    main()
