@echo off
if /i "%~1"=="--firefly-console" goto run
start "Firefly v1.0.1" "%ComSpec%" /d /k call "%~f0" --firefly-console
exit /b
:run
setlocal DisableDelayedExpansion
cd /d "%~dp0"
if errorlevel 1 goto fail
set "PYTHONUTF8=1"
set "PYTHONIOENCODING=utf-8"
where py >nul 2>&1
if not errorlevel 1 goto pylauncher
where python >nul 2>&1
if not errorlevel 1 goto python
if exist "%USERPROFILE%\miniforge3\python.exe" goto miniforge
if exist "%USERPROFILE%\miniconda3\python.exe" goto miniconda
echo Python was not found. Install Python 3.11 and enable Add Python to PATH.
goto fail
:pylauncher
py -3 "scripts\bootstrap_windows.py"
goto finish
:python
python "scripts\bootstrap_windows.py"
goto finish
:miniforge
"%USERPROFILE%\miniforge3\python.exe" "scripts\bootstrap_windows.py"
goto finish
:miniconda
"%USERPROFILE%\miniconda3\python.exe" "scripts\bootstrap_windows.py"
goto finish
:finish
set "FIREFLY_EXIT=%ERRORLEVEL%"
echo.
echo Firefly stopped. Exit code: %FIREFLY_EXIT%
echo Startup log: logs\startup.log
exit /b %FIREFLY_EXIT%
:fail
echo Startup failed. This window remains open so you can read the error.
exit /b 1
