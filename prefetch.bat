@echo off
rem Fetch and prepare the latest run of every model (or those named) into the
rem store, kept until a newer run is fetched: the globe opens with them loaded.
rem   prefetch.bat                               all models, all layers
rem   prefetch.bat ukv global ecmwf              only these models
rem   prefetch.bat ecmwf --layers rain_6h mslp   only these layers
setlocal
set "ROOT=%~dp0"
cd /d "%ROOT%"
set "ARGS=--prefetch %*"
if exist "%ROOT%nwp-globe-fetch.exe" (
    "%ROOT%nwp-globe-fetch.exe" %ARGS%
) else (
    call "%ROOT%_find_conda.bat"
    call "%CONDA_BAT%" run -n ukmo_globe --no-capture-output python globe.py %ARGS%
)
endlocal
