@echo off
rem Double-click this file to start the Question Cropper review page.
rem Tip: you can also drag a PDF (or a folder of PDFs) onto this file.
setlocal
cd /d "%~dp0"

set "PY=%~dp0.venv\Scripts\python.exe"

if not exist "%PY%" (
  echo The virtual environment is missing. Creating it now, this runs once...
  python -m venv .venv
  if not exist "%PY%" (
    echo.
    echo Could not create the virtual environment. Install Python 3.11 or newer
    echo from https://www.python.org/downloads/ and run this file again.
    echo.
    pause
    exit /b 1
  )
  "%PY%" -m pip install --upgrade pip
  "%PY%" -m pip install -r requirements.txt
  if errorlevel 1 (
    echo.
    echo Installing the dependencies failed. Check the messages above.
    echo.
    pause
    exit /b 1
  )
)

rem Scanned PDFs need Tesseract; add the usual install folder when it is not on PATH.
where tesseract >nul 2>nul
if errorlevel 1 if exist "%ProgramFiles%\Tesseract-OCR\tesseract.exe" set "PATH=%ProgramFiles%\Tesseract-OCR;%PATH%"

echo Starting Question Cropper. Your browser opens in a moment.
echo Keep this window open while you work. Press Ctrl+C or close it to stop.
echo.
"%PY%" -m cropper review %*
if errorlevel 1 (
  echo.
  echo The review page stopped with an error. The message above says what went wrong.
)
echo.
echo Question Cropper has stopped.
pause
