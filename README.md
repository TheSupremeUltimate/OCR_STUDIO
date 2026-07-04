# OCR Studio

OCR Studio is a premium, local, web-based GUI application that wraps the OlmOCR pipeline. It allows you to process PDF files page-by-page through a vision language model (like those hosted via LM Studio) and produce clean, formatted, merged Markdown output files, along with HTML and DOCX exports—all without touching the command line.

---

## Features

* **Drag-and-Drop Interface:** Easily upload single or multiple PDFs through a clean, glassmorphic dark-mode web dashboard.
* **Granular Progress Tracking:** Monitor real-time page-by-page progress status and estimated time of completion (ETA) via WebSockets.
* **Side-by-Side Preview:** View the generated Markdown text alongside the original rendered PDF page side-by-side.
* **Page Range Selection:** Process specific pages or ranges (e.g., `1-5, 8, 11-13`) to save time and API tokens.
* **Accuracy & Confidence Reports:** View average logprob confidence scores for each processed page.
* **Multi-Format Export:** Download your OCR results as Markdown (`.md`), HTML, or Word Document (`.docx`) formats.
* **Crash Recovery & True Resumption:** Automatically skips already-processed pages if a job is interrupted, saving inference time.

---

## Architecture

```
┌───────────────────────────┐
│  Browser (OCR Studio GUI) │
└─────────────┬─────────────┘
              │
      HTTP + WebSocket
              │
              ▼
┌───────────────────────────┐
│ FastAPI Backend (Python)  │
└─────────────┬─────────────┘
              │
      OpenAI-compatible API (HTTP)
              │
              ▼
┌───────────────────────────┐
│     LM Studio Server      │
│ (Hosts Vision LM Model)   │
└───────────────────────────┘
```

---

## Prerequisites

Before installing and running OCR Studio, ensure you have the following installed on your system:

1. **Python 3.12+**
   * Download and install from [python.org](https://www.python.org/).
   * Ensure Python is added to your system `PATH`.

2. **Poppler (for PDF Rendering)**
   * OCR Studio requires Poppler to render PDF pages into images.
   * **Windows Installation:**
     1. Download the latest Windows binary release (.zip) from [poppler-windows](https://github.com/oschwartz10612/poppler-windows/releases).
     2. Extract the downloaded folder (e.g., to `C:\poppler` or `D:\poppler`). Inside it, you will see a folder named `Library` containing a `bin` subfolder. Take note of this full path (e.g., `C:\poppler\Library\bin`).
     3. Add this `bin` folder path to your Windows Environment Variables:
        * Open the Start Menu, search for **"Edit the system environment variables"** (or type **"env"**) and select it.
        * Click the **"Environment Variables..."** button at the bottom of the dialog.
        * Under **"User variables"** (top section), look for a variable named **`Path`** (or `PATH`):
          * **If it exists:** Select it, click **"Edit..."**, click **"New"** on the right side of the list, and paste your full path (e.g., `C:\poppler\Library\bin`).
          * **If it does NOT exist:** Click **"New..."** (under User variables), enter **`Path`** as the Variable Name, and paste your full path (e.g., `C:\poppler\Library\bin`) as the Variable Value.
        * Click **"OK"** to save and close all three windows.
     4. Verify the setup by opening a **brand new** Command Prompt or PowerShell window and running:
        ```bash
        pdftoppm -h
        ```
        If you see help instructions instead of a "not recognized" error, Poppler is set up correctly.

3. **LM Studio (or another OpenAI-compatible inference server)**
   * Download and install from [lmstudio.ai](https://lmstudio.ai/).
   * Load a vision language model such as `allenai/olmocr-2-7b-1025` (or another vision-capable model like Llama 3.2 Vision).
   * Start the local server inside LM Studio.

---

## Installation

1. Clone or download this repository to your local machine (e.g., `D:\OCR_PROJECTS`).
2. Double-click the [setup_venv.bat](file:///d:/OCR_PROJECTS/setup_venv.bat) script in the root directory.
   * This script will:
     * Check if Python and Poppler are available.
     * Create a dedicated Python virtual environment (`venv`).
     * Install all required dependencies from [requirements.txt](file:///d:/OCR_PROJECTS/requirements.txt).
     * Create the necessary `output` and `logs` directories.

---

## How to Use

1. **Start the Inference Server:**
   * Open **LM Studio** (or your preferred server).
   * Load your vision model (e.g., `allenai_olmocr-2-7b-1025`).
   * Start the HTTP Server (usually defaults to `http://localhost:1234`).

2. **Launch OCR Studio:**
   * **Option A (Silent Mode - Recommended):** Double-click [start_silent.vbs](file:///d:/OCR_PROJECTS/start_silent.vbs) in the project root. This launches the application in the background without opening a command prompt window.
   * **Option B (Console Mode):** Double-click [start.bat](file:///d:/OCR_PROJECTS/start.bat) in the project root if you want to see the server output console.
   * *Note:* Both options will automatically open the application in your default web browser at `http://localhost:8080`.
   * *Auto-Shutdown:* Closing all browser tabs containing the OCR Studio UI will automatically shut down the background server after a 5-second grace period (allowing for page refreshes).

3. **Configure Settings:**
   * In the OCR Studio GUI, click the **Settings** icon.
   * Update the **Inference Server URL** and **Model Name** to match your running server. For example:
     * **URL:** `http://localhost:1234/v1` (or your remote/local IP)
     * **Model:** `allenai_olmocr-2-7b-1025`
   * Click **Save**.

4. **Run OCR Jobs:**
   * Drag and drop your PDF(s) into the upload area or click to browse.
   * Customize any optional settings (such as page ranges or specific output directories).
   * Click **Start Processing** and monitor the live progress.
   * Once finished, review the output using the side-by-side document preview, and download your preferred format (Markdown, HTML, or DOCX).

---

## License

This project is licensed under the MIT License:

```
MIT License

Copyright (c) 2026 OCR Studio Contributors

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

...
THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
```

**Third-Party Licenses:** Portions of the core OCR processing engine were adapted from [OlmOCR](https://github.com/allenai/olmocr), which is licensed under the Apache 2.0 License.