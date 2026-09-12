@echo off
REM ===================================================================
REM  Builds EasyMiniDirector.exe and EasyMiniDirector Setup.exe into dist\.
REM
REM  Each is a single self-contained .exe - no Python needed on the
REM  machine that runs it. Give someone dist\EasyMiniDirector Setup.exe and it works.
REM
REM  Run this whenever the code, the workflows or the translations
REM  change. It takes a couple of minutes.
REM ===================================================================
setlocal EnableExtensions
cd /d "%~dp0"
title Building EasyMiniDirector

REM  "Build EXE.bat nopause" skips the Press-any-key at the end, so this can
REM  be run from a script without leaving a window waiting for someone.
set "HOLD=pause"
if /i "%~1"=="nopause" set "HOLD=rem"

REM --- find Python ----------------------------------------------------
set "PY="
where py >nul 2>nul && set "PY=py"
if not defined PY where python >nul 2>nul && set "PY=python"
if not defined PY goto :no_python

echo.
echo ===================================================================
echo   Building EasyMiniDirector
echo ===================================================================
echo.

REM --- make sure the build tool is there ------------------------------
%PY% -c "import PyInstaller" >nul 2>nul
if errorlevel 1 (
    echo Installing PyInstaller, this happens once...
    %PY% -m pip install pyinstaller
    if errorlevel 1 goto :pyinstaller_failed
)

REM --- clean the previous build ---------------------------------------
if exist "build" rmdir /s /q "build"
if exist "dist" rmdir /s /q "dist"

REM -------------------------------------------------------------------
REM  Each build is driven by its .spec file, which lists the files to put
REM  inside the .exe and the libraries to leave out. The data layout must
REM  match what the code expects, because app\paths.py resolves it relative
REM  to the unpacked bundle; what is excluded, and why, is in
REM  build_common.py.
REM -------------------------------------------------------------------
echo [1/2] Building EasyMiniDirector.exe ...
%PY% -m PyInstaller --noconfirm --clean "EasyMiniDirector.spec"
if errorlevel 1 goto :build_failed

REM -------------------------------------------------------------------
REM  The installer second, and not by accident: it carries dist\EasyMiniDirector.exe
REM  inside itself as a data file, so the program has to exist first. The spec
REM  refuses to build if it does not.
REM -------------------------------------------------------------------
echo.
echo [2/2] Building EasyMiniDirector Setup.exe ...
%PY% -m PyInstaller --noconfirm --clean "EasyMiniDirector Setup.spec"
if errorlevel 1 goto :build_failed

REM --- tidy up --------------------------------------------------------

REM  Only the working folder. The .spec files are part of the source now -
REM  they say what goes in each build and what is left out - so deleting
REM  them, as this used to when PyInstaller generated them, would break
REM  the next run.
if exist "build" rmdir /s /q "build"

echo.
echo ===================================================================
echo   Done
echo ===================================================================
echo.
REM  Not just "Done": say what was actually produced, and check each exe
REM  against the newest source file. A stale exe is the one build mistake that
REM  looks exactly like a good one.
%PY% -X utf8 "%~dp0tools/report_build.py"
if errorlevel 1 goto :stale_build
echo.
echo Both are in:  %~dp0dist
echo.
echo These need nothing installed - not even Python. Copy the whole
echo dist folder, or just the one .exe you want to share.
echo.
echo Give people  EasyMiniDirector Setup.exe  - it carries the program inside it.
echo.
echo Note: the installer puts EasyMiniDirector.exe into your EasyAI folder,
echo and keeps its own settings and videos in an EasyMiniDirector subfolder.
echo.
%HOLD%
exit /b 0

REM -------------------------------------------------------------------
:stale_build
echo.
echo ===================================================================
echo   Build finished, but the result looks stale
echo ===================================================================
echo.
echo One of the .exe files is older than the code it was built from.
echo That usually means a build step failed further up, or a file was
echo saved while the build was running.
echo.
echo Run this again before giving these to anyone.
echo.
%HOLD%
exit /b 1

REM -------------------------------------------------------------------
:build_failed
echo.
echo Build failed. The messages above say why.
echo.
echo The usual causes:
echo   - antivirus holding a file open. Try again, or exclude this folder.
echo   - a previous EasyMiniDirector.exe still running. Close it and retry.
echo.
%HOLD%
exit /b 1

REM -------------------------------------------------------------------
:pyinstaller_failed
echo.
echo Could not install PyInstaller.
echo Try opening a Command Prompt here and running:
echo     %PY% -m pip install pyinstaller
echo.
%HOLD%
exit /b 1

REM -------------------------------------------------------------------
:no_python
echo.
echo Python is not installed, so nothing can be built.
echo Get it from  https://www.python.org/downloads/
echo and tick "Add python.exe to PATH" during setup.
echo.
%HOLD%
exit /b 1
