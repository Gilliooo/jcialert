@echo off
rem JCIAlert - portable setup. Self-contained: everything it needs is in this
rem folder, so it works from a USB stick, a network share or a Downloads
rem folder, with no build tree and no Inno Setup.
rem
rem Installs to %LOCALAPPDATA%\Programs\JCIAlert - your own profile, so no
rem admin and no UAC prompt. Program Files would need admin AND would break
rem Options -> Save, because config.json lives next to the exe.
setlocal
cd /d "%~dp0"

set "TARGET=%LOCALAPPDATA%\Programs\JCIAlert"

if not exist "JCIAlert.exe" (
  echo JCIAlert.exe is not in this folder. Copy the WHOLE folder, not just
  echo one file out of it.
  goto :fail
)
if not exist "emiten.json" (
  echo emiten.json is not in this folder. The app cannot match a single
  echo ticker without it and will refuse to start. Copy the whole folder.
  goto :fail
)

rem Windows locks a running exe, so replacing it would fail halfway.
tasklist /fi "imagename eq JCIAlert.exe" 2>nul | find /i "JCIAlert.exe" >nul
if not errorlevel 1 (
  echo JCIAlert is already running. Quit it from the tray first
  echo ^(right-click the icon -^> Quit^), then run this again.
  echo If you cannot see the icon, look under the ^^ chevron.
  goto :fail
)

echo Installing to %TARGET%
if not exist "%TARGET%" mkdir "%TARGET%" || goto :fail

copy /y "JCIAlert.exe" "%TARGET%\" >nul || goto :fail
copy /y "emiten.json"  "%TARGET%\" >nul || goto :fail
if exist "README.md" copy /y "README.md" "%TARGET%\" >nul

rem Settings and history are NEVER overwritten by a reinstall or an upgrade.
if not exist "%TARGET%\config.json"         copy /y "config.json"         "%TARGET%\" >nul
if not exist "%TARGET%\aliases_manual.json" copy /y "aliases_manual.json" "%TARGET%\" >nul

set "SM=%APPDATA%\Microsoft\Windows\Start Menu\Programs"
powershell -NoProfile -ExecutionPolicy Bypass -Command ^
  "$s=(New-Object -COM WScript.Shell).CreateShortcut('%SM%\JCIAlert.lnk');" ^
  "$s.TargetPath='%TARGET%\JCIAlert.exe';$s.WorkingDirectory='%TARGET%';" ^
  "$s.Description='JCIAlert - Indonesian market news alerts';$s.Save()" >nul 2>nul

echo.
echo Installed. Start Menu -^> JCIAlert
echo.
echo FIRST RUN IS QUIET BY DESIGN: each source's current backlog is recorded
echo and not alerted, so you do not get a hundred hours-old headlines.
echo.
echo NO TRAY ICON? Look under the ^^ chevron first - Windows 11 hides new
echo tray icons there. Drag it out to pin it. If it is genuinely not
echo running, this says why instead of failing silently:
echo     "%TARGET%\JCIAlert.exe" --doctor
echo.
echo Uninstall: REMOVE.bat in this folder.
start "" "%TARGET%\JCIAlert.exe"
exit /b 0

:fail
echo.
echo NOT INSTALLED.
pause
exit /b 1
