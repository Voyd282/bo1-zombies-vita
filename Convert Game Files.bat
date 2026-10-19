@echo off
setlocal
title BO1 Zombies for PS Vita - convert your game files
cd /d "%~dp0"

echo BO1 Zombies for PS Vita - game file converter
echo.
echo This makes the files the Vita needs from YOUR copy of Call of Duty: Black Ops (PC).
echo It only reads your game folder; the result goes to the "output" folder here.
echo.

rem Python 3: the py launcher first (the "python" command can be the Microsoft Store placeholder)
set "PY="
py -3 --version >nul 2>nul && set "PY=py -3"
if not defined PY (
    python --version >nul 2>nul && set "PY=python"
)
if not defined PY (
    echo Python 3 was not found. Install it from https://www.python.org/downloads/
    echo ^(tick "Add python.exe to PATH" in the installer^), then run this again.
    echo.
    pause
    exit /b 1
)

rem the game folder: dragged onto this file, or asked for
set "GAME=%~1"
if not defined GAME (
    echo Drag your Black Ops folder into this window ^(the folder with BlackOps.exe,
    echo Steam: steamapps\common\Call of Duty Black Ops^) and press Enter:
    set /p "GAME="
)
if defined GAME set "GAME=%GAME:"=%"
if not defined GAME (
    echo No folder given.
    pause
    exit /b 1
)

%PY% -I "%~dp0converter\convert.py" "%GAME%"
set "RESULT=%ERRORLEVEL%"
echo.
if "%RESULT%"=="0" (
    echo Finished. Copy the folder output\bo1zombies to ux0:data\ on your Vita.
) else (
    echo The conversion did not finish ^(see the message above^).
)
echo.
pause
exit /b %RESULT%
