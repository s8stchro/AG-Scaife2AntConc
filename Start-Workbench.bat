@echo off
setlocal
set "ROOT=%~dp0"
set "VENV=%LOCALAPPDATA%\AG-Scaife2AntConc\env\Scripts"
set "PYTHON=%VENV%\pythonw.exe"
if not exist "%PYTHON%" set "PYTHON=%VENV%\python.exe"
if not exist "%PYTHON%" (
  echo AG-Scaife2AntConc environment not found at %VENV%.
  pause
  exit /b 1
)
set "PYTHONPATH=%ROOT%ag-greek-rules;%ROOT%ag-diogenes-spacy-txt2xml;%ROOT%ag-scaife2antconc;%PYTHONPATH%"
start "" "%PYTHON%" -X utf8 -B -m ag_scaife2antconc.gui %*
