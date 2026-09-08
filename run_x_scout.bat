@echo off
setlocal
cd /d "%~dp0"
python -c "import webview" >nul 2>&1
if errorlevel 1 (
  echo.
  echo X Scout needs one local desktop UI package: pywebview
  echo Installing it now...
  echo.
  python -m pip install -r requirements.txt
  if errorlevel 1 (
    echo.
    echo Installation failed. Run: python -m pip install -r requirements.txt
    pause
    exit /b 1
  )
)
python launch.py
if errorlevel 1 pause
endlocal
