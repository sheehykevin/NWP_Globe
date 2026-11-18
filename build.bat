@echo off
rem ======================================================================
rem  Build the standalone globe into dist\NWP_Globe (one folder).
rem
rem    build.bat            build (creates the conda environment if needed)
rem    build.bat /update    update the environment from environment.yml first
rem
rem  Steps: environment, PyInstaller, GeoVista map data, pyflakes, build,
rem  smoke test. Close NWP_Globe.exe (and any file open from
rem  dist\NWP_Globe) before building: open files stop the build.
rem ======================================================================
setlocal EnableExtensions
set "ROOT=%~dp0"
cd /d "%ROOT%"
call "%ROOT%_find_conda.bat"
if not defined CONDA_BAT (
    echo ERROR: conda was not found. Install Miniforge or Anaconda first.
    goto :fail
)
set "ENV=ukmo_globe"
set "RUN=call "%CONDA_BAT%" run -n %ENV% --no-capture-output"
set "BUNDLE=dist\NWP_Globe"

echo [1/6] Conda environment %ENV%
call "%CONDA_BAT%" env list | findstr /b /c:"%ENV% " >nul
if errorlevel 1 (
    call "%CONDA_BAT%" env create -f environment.yml
    if errorlevel 1 goto :fail
) else if /i "%~1"=="/update" (
    call "%CONDA_BAT%" env update -n %ENV% -f environment.yml
    if errorlevel 1 goto :fail
)

echo [2/6] PyInstaller and pyflakes
rem pyvista and vtk stay pinned: newer ones break GeoVista 0.5.3.
%RUN% python -c "import PyInstaller, pyflakes" >nul 2>nul
if errorlevel 1 (
    call "%CONDA_BAT%" install -y -n %ENV% -c conda-forge pyinstaller pyflakes "pyvista=0.44.2" "vtk=9.3.1"
    if errorlevel 1 goto :fail
)

echo [3/6] GeoVista map data
%RUN% python packaging\prepare_geovista.py
if errorlevel 1 goto :fail

echo [4/6] pyflakes
%RUN% python -m pyflakes globe.py globe_core.py globe_data.py globe_fetch.py globe_icon.py globe_ecmwf.py globe_gfs.py globe_meteofrance.py globe_paths.py
if errorlevel 1 goto :fail

echo [5/6] Build
if exist "build" rmdir /s /q "build"
if exist "%BUNDLE%" rmdir /s /q "%BUNDLE%"
if exist "%BUNDLE%" (
    echo ERROR: %BUNDLE% could not be removed: close NWP_Globe.exe and any
    echo        file open from it, then run build.bat again.
    goto :fail
)
%RUN% python -m PyInstaller packaging\NWP_Globe.spec --noconfirm
if errorlevel 1 goto :fail

echo [6/6] Smoke test
"%BUNDLE%\nwp-globe-fetch.exe" --fetch --help >nul
if errorlevel 1 (
    echo ERROR: nwp-globe-fetch.exe did not start.
    goto :fail
)
start "" /wait "%BUNDLE%\NWP_Globe.exe" --smoke "%BUNDLE%\smoke_test.png" --smoke-wait 6
if not exist "%BUNDLE%\smoke_test.png" (
    echo ERROR: NWP_Globe.exe did not open its window.
    goto :fail
)
del "%BUNDLE%\smoke_test.png"

echo.
echo Build complete: %CD%\%BUNDLE%
echo Copy the whole NWP_Globe folder to the target computer and run
echo NWP_Globe.exe. See docs\CREDENTIALS.md for the wave models' login.
endlocal
exit /b 0

:fail
echo.
echo Build FAILED.
endlocal
exit /b 1
