@echo off
setlocal
cd /d "%~dp0"
set "PYTHON=.venv\Scripts\python.exe"
if not exist "%PYTHON%" (
  where py >nul 2>nul
  if errorlevel 1 (python -m venv .venv) else (py -3 -m venv .venv)
  if errorlevel 1 goto :fail
)
set "NEEDS_INSTALL=0"
if not exist ".venv\.flowcode-deps-version" set "NEEDS_INSTALL=1"
if exist ".venv\.flowcode-deps-version" (
  findstr /x /c:"0.25.0" ".venv\.flowcode-deps-version" >nul 2>nul
  if errorlevel 1 set "NEEDS_INSTALL=1"
)
"%PYTHON%" -c "import sys; sys.exit(0 if sys.version_info >= (3,10) else 1)" >nul 2>nul
if errorlevel 1 goto :versionfail
"%PYTHON%" -c "import eel, jedi, black, websocket" >nul 2>nul
if errorlevel 1 set "NEEDS_INSTALL=1"
if "%NEEDS_INSTALL%"=="1" (
  "%PYTHON%" -m pip install --disable-pip-version-check -r requirements.txt
  if errorlevel 1 goto :fail
  echo 0.25.0>".venv\.flowcode-deps-version"
)
"%PYTHON%" main.py
if errorlevel 1 goto :fail
goto :eof
:versionfail
echo.
echo FlowCode Studio requires Python 3.10 or newer. Install a supported version and try again.
pause
goto :eof
:fail
echo.
echo FlowCode Studio could not start. Check the error above.
pause
