@echo off
REM ==== Proxmark3 GUI launcher (Windows) ====
REM Starts the local web server and opens your browser.

setlocal
cd /d "%~dp0"

where py >nul 2>nul
if %errorlevel%==0 (
    py -3 "%~dp0pm3gui\server.py" %*
) else (
    python "%~dp0pm3gui\server.py" %*
)

if %errorlevel% neq 0 (
    echo.
    echo [!] The GUI server exited with an error.
    pause
)
endlocal
