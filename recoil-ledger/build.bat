@echo off
setlocal EnableExtensions
title Recoil Ledger - build

rem Absolute root, captured before anything can change the working directory.
set "ROOT=%~dp0"
if "%ROOT:~-1%"=="\" set "ROOT=%ROOT:~0,-1%"
cd /d "%ROOT%"

set "BUILD=%ROOT%\build"
set "OUT=%ROOT%\RecoilLedger"
set "EXE=%OUT%\RecoilLedger.exe"
set "LOG=%ROOT%\build.log"

echo.
echo   RECOIL // LEDGER  build
echo   -----------------------
echo   %ROOT%
echo.

rem ---- Visual Studio -------------------------------------------------------
set "VSWHERE=%ProgramFiles(x86)%\Microsoft Visual Studio\Installer\vswhere.exe"
if not exist "%VSWHERE%" goto :novs
set "VSDIR="
for /f "usebackq tokens=*" %%i in (`"%VSWHERE%" -latest -products * -requires Microsoft.VisualStudio.Component.VC.Tools.x86.x64 -property installationPath`) do set "VSDIR=%%i"
if not defined VSDIR goto :novc
echo [+] Visual Studio: %VSDIR%

rem vcvars can cd elsewhere and can return a stray errorlevel; pin the dir and check cl.exe instead.
set "VSCMD_START_DIR=%ROOT%"
call "%VSDIR%\VC\Auxiliary\Build\vcvars64.bat" >nul 2>&1
cd /d "%ROOT%"
where cl.exe >nul 2>&1 || goto :novc

rem ---- CMake ---------------------------------------------------------------
set "CMAKE=%VSDIR%\Common7\IDE\CommonExtensions\Microsoft\CMake\CMake\bin\cmake.exe"
if not exist "%CMAKE%" (
  where cmake.exe >nul 2>&1 || goto :nocmake
  set "CMAKE=cmake.exe"
)
set "GEN=Ninja"
where ninja.exe >nul 2>&1 || set "GEN=NMake Makefiles"
echo [+] Generator: %GEN%

rem A cache from a different generator (or an older run) breaks configure; start clean if so.
if exist "%BUILD%\CMakeCache.txt" (
  findstr /c:"CMAKE_GENERATOR:INTERNAL=%GEN%" "%BUILD%\CMakeCache.txt" >nul 2>&1 || rmdir /s /q "%BUILD%"
)

echo [+] Configuring...
"%CMAKE%" -S "%ROOT%" -B "%BUILD%" -G "%GEN%" -DCMAKE_BUILD_TYPE=Release > "%LOG%" 2>&1
if errorlevel 1 goto :buildfail

echo [+] Compiling (first build takes a minute)...
"%CMAKE%" --build "%BUILD%" --config Release >> "%LOG%" 2>&1
if errorlevel 1 goto :buildfail

rem Find the exe wherever the generator put it.
set "BUILT="
if exist "%BUILD%\RecoilLedger.exe" set "BUILT=%BUILD%\RecoilLedger.exe"
if not defined BUILT for /f "delims=" %%f in ('dir /s /b "%BUILD%\RecoilLedger.exe" 2^>nul') do if not defined BUILT set "BUILT=%%f"
if not defined BUILT (
  echo [x] Build reported success but RecoilLedger.exe was not produced.
  goto :showlog
)

rem ---- Package -------------------------------------------------------------
echo [+] Packaging...
taskkill /im RecoilLedger.exe /f >nul 2>&1
if not exist "%OUT%" mkdir "%OUT%"
copy /y "%BUILT%" "%EXE%" >nul
if errorlevel 1 (
  echo [x] Could not copy the exe to %OUT%
  echo     Close RecoilLedger if it is running, and check antivirus quarantine.
  goto :fail
)
xcopy "%ROOT%\data"   "%OUT%\data\"   /e /i /y /q >nul
xcopy "%ROOT%\assets" "%OUT%\assets\" /e /i /y /q >nul
if not exist "%OUT%\assets\weapons" mkdir "%OUT%\assets\weapons"

if not exist "%EXE%" (
  echo [x] %EXE% is missing after copy - antivirus probably removed it.
  echo     Add an exclusion for %OUT% and run build.bat again.
  goto :fail
)

echo.
echo   Done:  %EXE%
echo.
choice /c YN /n /m "  Launch now? [Y/N] "
if errorlevel 2 goto :done
start "" /d "%OUT%" "%EXE%"
goto :done

rem ---- Errors --------------------------------------------------------------
:novs
echo [x] Visual Studio not found.
echo     Install "Build Tools for Visual Studio 2022" with "Desktop development with C++":
echo     https://visualstudio.microsoft.com/downloads/#build-tools-for-visual-studio-2022
goto :fail

:novc
echo [x] The C++ toolset is missing.
echo     Open Visual Studio Installer, Modify, tick "Desktop development with C++".
goto :fail

:nocmake
echo [x] CMake not found. It ships with "Desktop development with C++"; or install it from cmake.org.
goto :fail

:buildfail
echo [x] Build failed.
:showlog
echo     Last lines of build.log:
echo     ------------------------
powershell -NoProfile -Command "Get-Content -LiteralPath '%LOG%' -Tail 25" 2>nul
echo.
echo     Full log: %LOG%
goto :fail

:fail
echo.
pause
exit /b 1

:done
endlocal
exit /b 0
