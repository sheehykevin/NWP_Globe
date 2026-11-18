@echo off
rem Pre-download and prepare the latest run of every model (or those named),
rem kept until the next pre-download: the globe then opens with them loaded.
rem   prefetch.bat                  all models
rem   prefetch.bat ukv global ecmwf only these
setlocal
set "ROOT=%~dp0"
cd /d "%ROOT%"
set "ARGS=--prefetch %*"
if /i "%~1"=="--prefetch-swap" set "ARGS=--prefetch-swap"
if exist "%ROOT%nwp-globe-fetch.exe" (
    "%ROOT%nwp-globe-fetch.exe" %ARGS%
) else (
    call "%ROOT%_find_conda.bat"
    call "%CONDA_BAT%" run -n ukmo_globe --no-capture-output python globe.py %ARGS%
)
endlocal
