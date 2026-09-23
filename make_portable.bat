@echo off
rem Assemble installer\JCIAlert-portable\ and zip it.
rem
rem THIS EXISTS BECAUSE THE FOLDER USED TO BE FILLED BY HAND, and a folder
rem filled by hand ships whatever was last dropped in it. The 1.0.1 zip went
rem out carrying the developer's own seen.json, news.csv, opened.json and
rem logs\ - so a colleague unzipping it would have started mid-history, on
rem someone else's story clusters, with someone else's logs to read.
rem
rem The fix is not "remember to delete those". It is to build the folder from
rem nothing every time, from an explicit list.
setlocal enabledelayedexpansion
cd /d "%~dp0"

if not exist "dist\JCIAlert.exe" (
  echo dist\JCIAlert.exe is missing - run build.bat first.
  goto :fail
)

rem One source of truth for the version, the same one the app reports.
for /f "delims=" %%V in ('python -c "import jci;print(jci.VERSION)"') do set "VER=%%V"
if not defined VER (
  echo Could not read the version from jci.py.
  goto :fail
)

set "OUT=installer\JCIAlert-portable"
echo Building %OUT% for %VER%

rem From nothing. Not a refresh of whatever is in there.
if exist "%OUT%" rd /s /q "%OUT%"
mkdir "%OUT%" || goto :fail

rem THE SHIP LIST. Program, ticker table, starting settings, docs. Nothing
rem else - seen.json, news.csv, opened.json, alerts.csv and logs\ are the
rem USER'S, and there is no such thing as a sensible starting value for them.
copy /y "dist\JCIAlert.exe"            "%OUT%\" >nul || goto :fail
copy /y "emiten.json"                  "%OUT%\" >nul || goto :fail
copy /y "config.json"                  "%OUT%\" >nul || goto :fail
copy /y "aliases_manual.json"          "%OUT%\" >nul || goto :fail
copy /y "README.md"                    "%OUT%\" >nul || goto :fail
copy /y "installer\portable\SETUP.bat"        "%OUT%\" >nul || goto :fail
copy /y "installer\portable\REMOVE.bat"       "%OUT%\" >nul || goto :fail
copy /y "installer\portable\READ-ME-FIRST.txt" "%OUT%\" >nul || goto :fail

set "ZIP=installer\JCIAlert-%VER%-portable.zip"
if exist "%ZIP%" del /q "%ZIP%"
powershell -NoProfile -ExecutionPolicy Bypass ^
  -Command "Compress-Archive -Path '%OUT%' -DestinationPath '%ZIP%' -Force" || goto :fail

echo.
echo Done:
for %%F in ("%ZIP%") do echo     %%~fF   ^(%%~zF bytes^)
echo.
echo Unzip anywhere and run SETUP.bat. No admin, no Inno Setup, no build tree.
exit /b 0

:fail
echo.
echo PORTABLE ZIP NOT BUILT.
exit /b 1
