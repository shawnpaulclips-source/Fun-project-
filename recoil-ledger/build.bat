@echo off
setlocal EnableDelayedExpansion
title Recoil Ledger - build
cd /d "%~dp0"

echo.
echo   RECOIL // LEDGER  build
echo   -----------------------
echo.

rem Locate Visual Studio (2019/2022, any edition incl. Build Tools) with the C++ toolset.
set "VSWHERE=%ProgramFiles(x86)%\Microsoft Visual Studio\Installer\vswhere.exe"
if not exist "%VSWHERE%" (
  echo [x] Visual Studio not found.
  echo     Install "Build Tools for Visual Studio 2022" with the "Desktop development with C++" workload:
  echo     https://visualstudio.microsoft.com/downloads/#build-tools-for-visual-studio-2022
  goto :fail
)
set "VSDIR="
for /f "usebackq tokens=*" %%i in (`"%VSWHERE%" -latest -products * -requires Microsoft.VisualStudio.Component.VC.Tools.x86.x64 -property installationPath`) do set "VSDIR=%%i"
if not defined VSDIR (
  echo [x] Visual Studio is installed but the C++ toolset is missing.
  echo     Open Visual Studio Installer and add "Desktop development with C++".
  goto :fail
)
echo [+] Visual Studio: %VSDIR%
call "%VSDIR%\VC\Auxiliary\Build\vcvars64.bat" >nul
if errorlevel 1 ( echo [x] vcvars64 failed & goto :fail )

rem CMake + Ninja ship with the C++ workload; fall back to a PATH cmake.
set "CMAKE=%VSDIR%\Common7\IDE\CommonExtensions\Microsoft\CMake\CMake\bin\cmake.exe"
if not exist "%CMAKE%" set "CMAKE=cmake"
set "GEN=Ninja"
where ninja >nul 2>nul || set "GEN=NMake Makefiles"
echo [+] Generator: %GEN%

echo [+] Configuring...
"%CMAKE%" -S . -B build -G "%GEN%" -DCMAKE_BUILD_TYPE=Release >build.log 2>&1
if errorlevel 1 ( echo [x] Configure failed - see build.log & goto :fail )

echo [+] Compiling...
"%CMAKE%" --build build >>build.log 2>&1
if errorlevel 1 ( echo [x] Build failed - see build.log & goto :fail )

echo [+] Packaging...
set "OUT=%~dp0RecoilLedger"
if exist "%OUT%\RecoilLedger.exe" del /q "%OUT%\RecoilLedger.exe"
if not exist "%OUT%" mkdir "%OUT%"
copy /y "build\RecoilLedger.exe" "%OUT%\" >nul
xcopy /e /i /y /q "data" "%OUT%\data" >nul
xcopy /e /i /y /q "assets" "%OUT%\assets" >nul
if not exist "%OUT%\assets\weapons" mkdir "%OUT%\assets\weapons"

echo.
echo   Done.  %OUT%\RecoilLedger.exe
echo.
choice /c YN /n /m "  Launch now? [Y/N] "
if errorlevel 2 goto :eof
start "" "%OUT%\RecoilLedger.exe"
goto :eof

:fail
echo.
pause
exit /b 1
