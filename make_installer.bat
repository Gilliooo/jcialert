@echo off
rem Build JCIAlert-Setup-<version>.exe
rem
rem Order matters: suites, then exe, then installer. Shipping a setup built
rem around a broken exe is worse than shipping nothing, because it reaches
rem other people's machines.
setlocal enabledelayedexpansion
cd /d "%~dp0"

echo.
echo === 1/3  tests and JCIAlert.exe ===
call build.bat || goto :fail

if not exist "dist\JCIAlert.exe" (
  echo build.bat finished but dist\JCIAlert.exe is not there.
  goto :fail
)

echo.
echo === 2/4  checking the installer script against the code ===
python test_installer.py || goto :fail

echo.
echo === 3/4  the portable zip ===
rem Built BEFORE the Inno step, and unconditionally: it is the route that
rem works on a machine with no Inno Setup, and the fallback message at the
rem bottom of this file promises it exists. Promising a file you only build
rem when everything else succeeded is how that message starts lying.
call make_portable.bat || goto :fail

echo.
echo === 4/4  compiling the installer ===

rem ---------------------------------------------------------------------------
rem FINDING ISCC.EXE. The first version of this guessed three paths and, when
rem it missed, said "Inno Setup is not installed" - which was wrong, and wrong
rem in the worst direction: it blamed the user's machine for the script's own
rem narrow guess. It now asks the REGISTRY, which is where Inno Setup records
rem itself, tries both hives and both bitness views, and if it still cannot
rem find the compiler it PRINTS EVERY PATH IT TRIED instead of asserting a
rem conclusion it has not earned.
rem ---------------------------------------------------------------------------
set "ISCC="
set "TRIED="

rem 1. On PATH?
for /f "delims=" %%I in ('where iscc 2^>nul') do if not defined ISCC set "ISCC=%%I"
if defined ISCC echo   found on PATH: !ISCC!

rem 2. The registry. Inno Setup 6 and 5 both write an InstallLocation here,
rem    under HKLM for an all-users install and HKCU for a per-user one.
if not defined ISCC (
  for %%H in (HKLM HKCU) do (
    for %%V in ("" " /reg:32" " /reg:64") do (
      for %%K in ("Inno Setup 6_is1" "Inno Setup 5_is1") do (
        for /f "tokens=2,*" %%A in (
          'reg query "%%H\SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall\%%~K" /v InstallLocation%%~V 2^>nul ^| find "InstallLocation"'
        ) do (
          if not defined ISCC if exist "%%B\ISCC.exe" (
            set "ISCC=%%B\ISCC.exe"
            echo   found via registry ^(%%H, %%~K^): !ISCC!
          )
        )
      )
    )
  )
)

rem 3. The usual folders, including the versionless and per-user ones.
if not defined ISCC (
  for %%P in (
    "%ProgramFiles(x86)%\Inno Setup 6\ISCC.exe"
    "%ProgramFiles%\Inno Setup 6\ISCC.exe"
    "%ProgramFiles(x86)%\Inno Setup\ISCC.exe"
    "%ProgramFiles%\Inno Setup\ISCC.exe"
    "%ProgramFiles(x86)%\Inno Setup 5\ISCC.exe"
    "%LOCALAPPDATA%\Programs\Inno Setup 6\ISCC.exe"
    "%LOCALAPPDATA%\Programs\Inno Setup\ISCC.exe"
    "%USERPROFILE%\AppData\Local\Programs\Inno Setup 6\ISCC.exe"
  ) do (
    set "TRIED=!TRIED!    %%~P!LF!"
    if not defined ISCC if exist %%P (
      set "ISCC=%%~P"
      echo   found: !ISCC!
    )
  )
)

if not defined ISCC (
  echo.
  echo Could not find ISCC.exe - the Inno Setup COMMAND-LINE compiler.
  echo.
  echo This does not necessarily mean Inno Setup is missing. ISCC.exe is a
  echo separate file from the Compil32.exe GUI, and some installs put it
  echo somewhere these did not look:
  echo.
  echo !TRIED!
  echo   Find it yourself with:
  echo       where /r "C:\Program Files (x86)" ISCC.exe
  echo       where /r "C:\Program Files" ISCC.exe
  echo.
  echo   Then either add that folder to PATH, or run the compile by hand:
  echo       "full\path\to\ISCC.exe" "installer\JCIAlert.iss"
  echo.
  echo   If Inno Setup really is not installed, get it from
  echo       https://jrsoftware.org/isdl.php   ^(free^)
  echo.
  echo Meanwhile step 3 already built the portable zip, which installs the
  echo app with no extra tooling at all - unzip it and run SETUP.bat:
  for %%F in ("installer\*-portable.zip") do echo       %%~fF
  goto :fail
)

echo.
"%ISCC%" "installer\JCIAlert.iss"
set "RC=%ERRORLEVEL%"
if not "%RC%"=="0" (
  echo.
  echo ISCC exited with code %RC% - the error it printed above is the reason.
  echo Nothing was written to installer\Output\.
  goto :fail
)

if not exist "installer\Output\*.exe" (
  echo.
  echo ISCC reported success but installer\Output\ has no .exe in it.
  echo Check the OutputDir line in installer\JCIAlert.iss.
  goto :fail
)

echo.
echo Done:
for %%F in ("installer\Output\*.exe") do echo     %%~fF   ^(%%~zF bytes^)
echo.
echo NOTE: it is not code-signed, so Windows SmartScreen will warn the first
echo       time anyone runs it ^("More info" -^> "Run anyway"^). Signing needs
echo       a certificate; nothing in the build can work around it.
exit /b 0

:fail
echo.
echo INSTALLER NOT BUILT.
exit /b 1
