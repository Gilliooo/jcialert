@echo off
rem JCIAlert - remove what SETUP.bat installed.
rem
rem Settings and history are KEPT unless you pass /purge. Losing seen.json
rem means a fresh install re-reads every feed as if it were new, so deleting
rem it should be a decision, not a side effect.
setlocal
set "TARGET=%LOCALAPPDATA%\Programs\JCIAlert"
set "SM=%APPDATA%\Microsoft\Windows\Start Menu\Programs\JCIAlert.lnk"

tasklist /fi "imagename eq JCIAlert.exe" 2>nul | find /i "JCIAlert.exe" >nul
if not errorlevel 1 (
  echo JCIAlert is running. Quit it from the tray first ^(right-click, Quit^).
  pause
  exit /b 1
)

rem The autostart entry must go, or Windows keeps trying to launch a program
rem that is no longer there at every login.
reg delete "HKCU\Software\Microsoft\Windows\CurrentVersion\Run" /v JCIAlert /f >nul 2>nul

if exist "%SM%" del /q "%SM%"
if exist "%USERPROFILE%\Desktop\JCIAlert.lnk" del /q "%USERPROFILE%\Desktop\JCIAlert.lnk"

del /q "%TARGET%\JCIAlert.exe" 2>nul
del /q "%TARGET%\emiten.json" 2>nul
del /q "%TARGET%\README.md" 2>nul

if /i "%~1"=="/purge" (
  echo Deleting settings and history as well.
  rd /s /q "%TARGET%" 2>nul
  rd /s /q "%LOCALAPPDATA%\JCIAlert" 2>nul
  echo Removed everything.
) else (
  echo Removed the program. Settings and history are still in:
  echo     %TARGET%
  echo Run  REMOVE.bat /purge  to delete those too.
)
pause
exit /b 0
