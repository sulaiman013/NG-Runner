@echo off
rem NG Runner Setup Check, started by a double-click.
rem Why this file: Windows will not run a .ps1 on a double-click with its default settings. This
rem runs setup_check.ps1 from this same folder for this one run only (-ExecutionPolicy Bypass
rem changes no setting) and keeps the window open at the end so the result can be read.
rem Options pass through, for example: setup_check.cmd -OutDir "D:\Reports" -NoPause
rem No labels or goto: cmd.exe finds labels unreliably in a file with LF line endings, which a
rem git checkout can produce, and straight-line code like this does not care.
setlocal
title NG Runner Setup Check
set "NGR_PS1=%~dp0setup_check.ps1"
set "NGR_CODE=2"
if not exist "%NGR_PS1%" echo setup_check.ps1 is missing. Put it in the same folder as this file, then try again.
if exist "%NGR_PS1%" powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%NGR_PS1%" %*
if exist "%NGR_PS1%" set "NGR_CODE=%ERRORLEVEL%"
rem The script's exit code: 0, the report was saved; 3, it could not be saved and the window
rem already says what to do. Any other code means the script stopped before the end.
if exist "%NGR_PS1%" if not "%NGR_CODE%"=="0" if not "%NGR_CODE%"=="3" echo The check stopped before it finished. Take a photo of this window and put it in your private Drive folder for Sulaiman.
rem The script waits for Enter by itself only when Explorer starts it directly. Here cmd.exe
rem starts it, so this is the only wait, and -NoPause skips it.
set "NGR_WAIT=1"
for %%A in (%*) do if /i "%%~A"=="-NoPause" set "NGR_WAIT="
if defined NGR_WAIT echo.
if defined NGR_WAIT echo Press any key to close this window.
if defined NGR_WAIT pause >nul
exit /b %NGR_CODE%
