@echo off
echo ============================================
echo   OCR Studio - Environment Setup
echo ============================================
echo.

:: Check Python is available
python --version >nul 2>&1
if errorlevel 1 (
    echo [ERROR] Python is not installed or not on PATH.
    echo Please install Python 3.12+ from https://www.python.org/
    pause
    exit /b 1
)

:: Check poppler (pdftoppm) is available
pdftoppm -h >nul 2>&1
if errorlevel 1 (
    echo [WARNING] poppler pdftoppm is not found on PATH.
    echo OCR Studio requires poppler for PDF page rendering.
    echo.
    echo Install poppler:
    echo   1. Download from https://github.com/oschwartz10612/poppler-windows/releases
    echo   2. Extract and add the 'bin' folder to your system PATH.
    echo.
    echo Continuing setup, but OCR processing will fail without poppler.
    echo.
)

:: Create virtual environment
if not exist "venv" (
    echo [1/3] Creating virtual environment...
    python -m venv venv
    if errorlevel 1 (
        echo [ERROR] Failed to create virtual environment.
        pause
        exit /b 1
    )
    echo       Done.
) else (
    echo [1/3] Virtual environment already exists, skipping creation.
)

:: Activate and install dependencies
echo [2/3] Installing dependencies...
call venv\Scripts\activate
pip install -r requirements.txt --quiet
if errorlevel 1 (
    echo [ERROR] Failed to install dependencies.
    pause
    exit /b 1
)
echo       Done.

:: Create output and logs directories
echo [3/3] Creating project directories...
if not exist "output" mkdir output
if not exist "logs" mkdir logs
echo       Done.

echo.
echo ============================================
echo   Setup complete!
echo   Run 'start.bat' to launch OCR Studio.
echo ============================================
pause
