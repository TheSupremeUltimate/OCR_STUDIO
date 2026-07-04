import logging
from pathlib import Path
import re
import markdown

logger = logging.getLogger("ocr_studio.export_utils")

# Styling boilerplate for HTML export (Includes custom page break layouts)
HTML_TEMPLATE = """<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>OCR Result</title>
  <style>
    body {{
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
      line-height: 1.6;
      color: #333;
      max-width: 800px;
      margin: 40px auto;
      padding: 0 20px;
      background-color: #fafafa;
    }}
    .content {{
      background: #fff;
      padding: 40px;
      border-radius: 8px;
      box-shadow: 0 4px 6px rgba(0,0,0,0.05), 0 1px 3px rgba(0,0,0,0.1);
    }}
    h1, h2, h3, h4, h5, h6 {{
      color: #111;
      margin-top: 24px;
      margin-bottom: 16px;
      font-weight: 600;
      line-height: 1.25;
    }}
    h1 {{ font-size: 2em; border-bottom: 1px solid #eaecef; padding-bottom: 0.3em; }}
    h2 {{ font-size: 1.5em; border-bottom: 1px solid #eaecef; padding-bottom: 0.3em; }}
    p, ul, ol {{
      margin-top: 0;
      margin-bottom: 16px;
    }}
    code {{
      font-family: SFMono-Regular, Consolas, "Liberation Mono", Menlo, monospace;
      font-size: 85%;
      background-color: rgba(27,31,35,0.05);       padding: 0.2em 0.4em;
      border-radius: 3px;
    }}
    pre {{
      background-color: #f6f8fa;
      padding: 16px;
      border-radius: 6px;
      overflow: auto;
      margin-bottom: 16px;
    }}
    pre code {{
      background-color: transparent;
      padding: 0;
      font-size: 100%;
    }}
    blockquote {{
      margin: 0 0 16px 0;
      padding: 0 1em;
      color: #6a737d;
      border-left: 0.25em solid #dfe2e5;
    }}
    table {{
      border-collapse: collapse;
      width: 100%;
      margin-bottom: 16px;
    }}
    table th, table td {{
      border: 1px solid #dfe2e5;
      padding: 6px 13px;
    }}
    table tr:nth-child(even) {{
      background-color: #f6f8fa;
    }}
    table th {{
      font-weight: 600;
      background-color: #f2f2f2;
    }}
    /* Aesthetic structural page breaks */
    .html-page-divider {{
      display: flex;
      align-items: center;
      margin: 50px 0 30px 0;
      color: #0ea5e9;
      font-weight: 700;
      font-size: 0.8rem;
      letter-spacing: 0.1em;
      text-transform: uppercase;
      user-select: none;
      page-break-before: always; /* Forces hard page split when printing or saving to PDF */
    }}
    .html-page-divider::after {{
      content: "";
      flex: 1;
      margin-left: 20px;
      border-bottom: 2px dashed #eaecef;
    }}
  </style>
</head>
<body>
  <div class="content">
    {body}
  </div>
</body>
</html>
"""

def convert_markdown_to_html(md_path: Path, html_path: Path) -> bool:
    """Convert Markdown file to a styled HTML document with paragraph and page-break support."""
    try:
        md_text = md_path.read_text(encoding="utf-8")
        
        # Pre-process: Intercept hidden page comments and convert them into structural HTML layout markers
        md_text = re.sub(
            r'<!--\s*PAGE\s+(\d+)\s*-->', 
            r'<div class="html-page-divider">Page \1</div>', 
            md_text
        )
        
        # Added 'nl2br' extension to translate single model line returns into structural line breaks natively
        body_html = markdown.markdown(md_text, extensions=['tables', 'fenced_code', 'nl2br'])
        
        full_html = HTML_TEMPLATE.format(body=body_html)
        html_path.write_text(full_html, encoding="utf-8")
        logger.info("Successfully converted %s to HTML -> %s", md_path.name, html_path.name)
        return True
    except Exception as e:
        logger.error("Failed to convert %s to HTML: %s", md_path.name, e, exc_info=True)
        return False

def convert_markdown_to_docx(md_path: Path, docx_path: Path) -> bool:
    """Convert Markdown file to a DOCX document using pypandoc."""
    try:
        import os
        os.environ.pop("GITHUB_TOKEN", None)
        import pypandoc
        
        try:
            pypandoc.get_pandoc_path()
        except OSError:
            logger.info("Pandoc not found. Initiating automatic download...")
            pypandoc.download_pandoc()
            logger.info("Pandoc download completed successfully.")
            
        pypandoc.convert_file(
            source_file=str(md_path),
            to='docx',
            outputfile=str(docx_path)
        )
        logger.info("Successfully converted %s to DOCX -> %s", md_path.name, docx_path.name)
        return True
    except Exception as e:
        logger.error("Failed to convert %s to DOCX: %s", md_path.name, e, exc_info=True)
        return False