@echo off
rem Sets CONDA_BAT to a usable conda launcher, or leaves it empty.
rem Called by build.bat.
rem
rem condabin\conda.bat is preferred to Scripts\conda.exe: from a cmd
rem window where "conda init" has not been run, conda.exe cannot activate
rem an environment, so "conda run" and pip installs fail.
set "CONDA_BAT="
set "CONDA_FROM_EXE="
if defined CONDA_EXE for %%E in ("%CONDA_EXE%") do (
    set "CONDA_FROM_EXE=%%~dpE..\condabin\conda.bat"
)
for %%P in (
    "%CONDA_FROM_EXE%"
    "%USERPROFILE%\miniforge3\condabin\conda.bat"
    "%USERPROFILE%\miniconda3\condabin\conda.bat"
    "%USERPROFILE%\anaconda3\condabin\conda.bat"
    "%LOCALAPPDATA%\miniforge3\condabin\conda.bat"
    "%ProgramData%\miniforge3\condabin\conda.bat"
    "%ProgramData%\miniconda3\condabin\conda.bat"
    "%ProgramData%\anaconda3\condabin\conda.bat"
) do (
    if not defined CONDA_BAT if exist "%%~P" set "CONDA_BAT=%%~fP"
)
if not defined CONDA_BAT (
    for /f "delims=" %%W in ('where conda.bat 2^>nul') do (
        if not defined CONDA_BAT set "CONDA_BAT=%%W"
    )
)
if not defined CONDA_BAT (
    where conda >nul 2>nul && set "CONDA_BAT=conda"
)
set "CONDA_FROM_EXE="
exit /b 0
