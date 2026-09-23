@echo off
rem Compile the installer ONLY - no tests, no PyInstaller.
rem
rem For when build.bat has already produced dist\JCIAlert.exe and the only
rem thing left is turning JCIAlert.iss into a setup .exe. Also the thing to
rem run when make_installer.bat cannot find ISCC and you want to point at it
rem by hand:
rem
rem     compile_installer.bat "C:\Program Files (x86)\Inno Setup 6\ISCC.exe"
rem
setlocal
cd /d "%~dp0"

if not exist "dist\JCIAlert.exe" (
  echo dist\JCIAlert.exe is missing - run build.bat first.
  goto :fail
)

set "ISCC=%~1"
if defined ISCC (
  if not exist "%ISCC%" (
    echo That path does not exist:  %ISCC%
    goto :fail
  )
  echo Using the compiler you gave me: %ISCC%
) else (
  for /f "delims=" %%I in ('where iscc 2^>nul') do if not defined ISCC set "ISCC=%%I"
  if not defined ISCC for %%P in (
    "%ProgramFiles(x86)%\Inno Setup 6\ISCC.exe"
    "%ProgramFiles%\Inno Setup 6\ISCC.exe"
    "%ProgramFiles(x86)%\Inno Setup\ISCC.exe"
    "%LOCALAPPDATA%\Programs\Inno Setup 6\ISCC.exe"
  ) do if not defined ISCC if exist %%P set "ISCC=%%~P"
)

if not defined ISCC (
  echo.
  echo ISCC.exe not found. Find it and pass it in:
  echo     where /r "C:\Program Files (x86)" ISCC.exe
  echo     compile_installer.bat "the\path\it\printed"
  goto :fail
)

echo.
"%ISCC%" "installer\JCIAlert.iss" || goto :fail

echo.
echo Done:
for %%F in ("installer\Output\*.exe") do echo     %%~fF   ^(%%~zF bytes^)
exit /b 0

:fail
echo.
echo NOT COMPILED.
exit /b 1
