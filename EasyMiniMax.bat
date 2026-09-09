@echo off
REM ===================================================================
REM  EasyMiniMax launcher
REM  Double-click this file to start EasyMiniMax.
REM  It finds Python, installs anything missing the first time, then
REM  opens the app without leaving a black console window behind.
REM ===================================================================
setlocal EnableExtensions
cd /d "%~dp0"
title EasyMiniMax

REM --- find a Python we can use --------------------------------------
REM "py" is the launcher that ships with python.org installs; "python"
REM covers Microsoft Store and custom installs.
set "PY="
set "PYW="
where py >nul 2>nul && set "PY=py"
if not defined PY where python >nul 2>nul && set "PY=python"
if not defined PY goto :no_python

REM The w-suffixed copies run without a console window.
where pyw >nul 2>nul && set "PYW=pyw"
if not defined PYW where pythonw >nul 2>nul && set "PYW=pythonw"
if not defined PYW set "PYW=%PY%"

REM --- check the version ---------------------------------------------
%PY% -c "import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)" >nul 2>nul
if errorlevel 1 goto :old_python

REM --- check the libraries EasyMiniMax needs -------------------------------
%PY% -c "import PySide6, requests, websocket, PIL" >nul 2>nul
if errorlevel 1 goto :install

:run
echo Starting EasyMiniMax...
REM Launched with the windowless Python so no black box is left behind.
REM If startup fails, EasyMiniMax.py puts the error in a message box, because
REM nothing printed here would ever be seen.
start "EasyMiniMax" %PYW% "%~dp0EasyMiniMax.py"
exit /b 0

REM -------------------------------------------------------------------
:install
echo.
echo ===================================================================
echo   First time setup
echo ===================================================================
echo.
echo EasyMiniMax needs a few Python libraries. This happens once and takes
echo a couple of minutes. Leave this window open.
echo.
pause
%PY% -m pip install --upgrade pip
%PY% -m pip install -r "%~dp0requirements.txt"
if errorlevel 1 goto :install_failed
echo.
echo Setup finished.
echo.
goto :run

REM -------------------------------------------------------------------
:install_failed
echo.
echo Could not install the libraries EasyMiniMax needs.
echo.
echo Try opening a Command Prompt in this folder and running:
echo     %PY% -m pip install -r requirements.txt
echo.
echo If that mentions permissions, close other programs and try again.
echo.
pause
exit /b 1

REM -------------------------------------------------------------------
:no_python
echo.
echo ===================================================================
echo   Python is not installed
echo ===================================================================
echo.
echo EasyMiniMax needs Python 3.10 or newer.
echo.
echo 1. Go to  https://www.python.org/downloads/
echo 2. Download and run the installer.
echo 3. IMPORTANT: tick "Add python.exe to PATH" on the first screen.
echo 4. When it finishes, double-click EasyMiniMax.bat again.
echo.
pause
exit /b 1

REM -------------------------------------------------------------------
:old_python
echo.
echo ===================================================================
echo   Python is too old
echo ===================================================================
echo.
for /f "delims=" %%V in ('%PY% -c "import sys;print(sys.version.split()[0])"') do echo You have Python %%V, but EasyMiniMax needs 3.10 or newer.
echo.
echo Install a newer version from  https://www.python.org/downloads/
echo and remember to tick "Add python.exe to PATH".
echo.
pause
exit /b 1
