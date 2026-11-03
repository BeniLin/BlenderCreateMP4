@echo off
rem animator launcher: runs src\animator with the project .venv Python (no pip install of this project needed).
setlocal
set "ROOT=%~dp0"
set "PY=%ROOT%.venv\Scripts\python.exe"
if not exist "%PY%" (
  echo [FAIL] Python venv not found: %PY%  ^(see README: deployment^)
  exit /b 1
)
set "PYTHONPATH=%ROOT%src;%PYTHONPATH%"
"%PY%" -m animator %*
exit /b %ERRORLEVEL%
