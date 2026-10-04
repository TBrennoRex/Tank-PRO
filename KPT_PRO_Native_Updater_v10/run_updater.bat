@echo off
setlocal
cd /d "%~dp0"
py -3 -m pip install -r requirements.txt
if errorlevel 1 (
    echo.
    echo ERRO: nao foi possivel instalar o backend WinRT MIDI.
    pause
    exit /b 1
)
py -3 app.py
if errorlevel 1 pause
