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
   * **Windows:**
     1. Download the latest Windows binary release (.zip) from [poppler-windows](https://github.com/oschwartz10612/poppler-windows/releases).
     2. Extract the downloaded folder (e.g., to `C:\poppler`). Inside it, find the `bin` subfolder (e.g., `C:\poppler\Library\bin`).
     3. Add this `bin` folder path to your Windows Environment Variables under the `Path` variable.
     4. Verify by opening a new Command Prompt and running: `pdftoppm -h`
   * **macOS:**
     Install via Homebrew:
     ```bash
     brew install poppler
     ```
   * **Linux (Debian/Ubuntu):**
     Install via apt:
     ```bash
     sudo apt-get update
     sudo apt-get install poppler-utils
     ```

3. **LM Studio (or another OpenAI-compatible inference server)**
   * Download and install from [lmstudio.ai](https://lmstudio.ai/).
   * Load a vision language model such as `allenai/olmocr-2-7b-1025` (or another vision-capable model like Llama 3.2 Vision).
   * Start the local server inside LM Studio.

---

## Installation

1. Clone or download this repository to your local machine.
2. Set up the Python environment based on your operating system:

**For Windows:**
Double-click the `setup_venv.bat` script in the root directory. This will automatically create the virtual environment, install dependencies, and create the necessary folders.

**For macOS / Linux:**
Open your terminal, navigate to the project directory, and run:
```bash
# Create and activate the virtual environment
python3 -m venv venv
source venv/bin/activate

# Install the Python dependencies
pip install -r requirements.txt

# Create the necessary project directories
mkdir -p output/uploads logs

```

---

## How to Use

1. **Start the Inference Server:**
* Open **LM Studio**.
* Load your vision model (e.g., `allenai_olmocr-2-7b-1025`).
* Start the HTTP Server (usually defaults to `http://localhost:1234`).


2. **Launch OCR Studio:**
**On Windows:**
* **Silent Mode (Recommended):** Double-click `start_silent.vbs`. This launches the application in the background without opening a command prompt window.
* **Console Mode:** Double-click `start.bat` if you want to see the server output console.
* *Note: Closing all browser tabs containing the OCR Studio UI will automatically shut down the background server after 5 seconds.*


**On macOS / Linux:**
* Open your terminal and activate the virtual environment:
```bash
source venv/bin/activate

```


* Start the FastAPI server:
```bash
uvicorn backend.main:app --host 127.0.0.1 --port 8080

```


* Open your web browser and navigate to `http://localhost:8080`.


3. **Configure Settings:**
* In the OCR Studio GUI, click the **Settings** icon.
* Update the **Inference Server URL** and select your **Model Name** from the dynamic dropdown.
* Click **Save**.


4. **Run OCR Jobs:**
* Drag and drop your PDF(s) into the upload area or click to browse.
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