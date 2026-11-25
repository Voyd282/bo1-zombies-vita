@echo off
rem BO1 Zombies for PS Vita - the converter window (converter\converter_gui.py): Python 3 with Tkinter
rem (the python.org installer has it). It reads your Black Ops folder and writes the files for the Vita.
cd /d "%~dp0"
set "PYW="
where pyw >nul 2>nul && set "PYW=pyw -3"
if not defined PYW (
    where pythonw >nul 2>nul && set "PYW=pythonw"
)
if not defined PYW (
    echo Python 3 was not found. Install it from https://www.python.org/downloads/
    echo ^(tick "Add python.exe to PATH" in the installer^), then run this again.
    pause
    exit /b 1
)
start "" %PYW% "%~dp0converter\converter_gui.py"
