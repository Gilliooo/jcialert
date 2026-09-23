@echo off
rem Build JCIAlert.exe - single file, no console.
cd /d "%~dp0"

rem ---------------------------------------------------------------------------
rem WINDOWS LOCKS A RUNNING EXE, and PyInstaller does not find that out until
rem the very last step - after the suites, the dependency install and ~30
rem seconds of analysis - where it dies on
rem     PermissionError: [WinError 5] Access is denied: dist\JCIAlert.exe
rem which names a permission problem rather than the actual cause. Check it
rem FIRST: the answer is two seconds of work, and finding out at the end costs
rem a whole build. installer\install.bat already did this; the build did not.
rem ---------------------------------------------------------------------------
tasklist /fi "imagename eq JCIAlert.exe" 2>nul | find /i "JCIAlert.exe" >nul
if not errorlevel 1 (
  echo.
  echo JCIAlert is RUNNING, so dist\JCIAlert.exe cannot be replaced.
  echo.
  echo   Right-click the tray icon -^> Quit, then run this again.
  echo   If you cannot see the icon, look under the ^^ chevron - Windows
  echo   hides new tray icons there.
  echo   Last resort:  taskkill /im JCIAlert.exe /f
  echo.
  goto :fail
)

rem ONE list of suites, in run_tests.bat. This file used to carry a second
rem copy of it, kept in step by hand - and a suite added to one and not the
rem other is a suite the build stops running without telling anyone.
echo Running every suite first - a broken build is worse than no build.
call run_tests.bat || goto :fail
echo.

if not exist "emiten.json" (
  echo emiten.json is missing - the app cannot match a single ticker without it.
  echo Run:  python build_aliases.py --report
  goto :fail
)

echo Installing build dependencies...
python -m pip install --quiet --upgrade pystray pillow pyinstaller || goto :fail

echo Building JCIAlert.exe ...
python -m PyInstaller --noconfirm --clean ^
  --onefile ^
  --noconsole ^
  --name JCIAlert ^
  --hidden-import pystray._win32 ^
  --collect-submodules tkinter ^
  --hidden-import jci ^
  --hidden-import jcinet ^
  --hidden-import jcisource ^
  --hidden-import jciengine ^
  --hidden-import jcimatch ^
  --hidden-import jcifilter ^
  --hidden-import jciview ^
  --hidden-import jcioptions ^
  --hidden-import jciwindow ^
  --hidden-import jcipopup ^
  --hidden-import jcistartup ^
  --hidden-import jcidoctor ^
  jcitray.py || goto :fail

rem emiten.json is DATA, not code, and must stay refreshable - bundling it
rem inside the exe would freeze the ticker table at build time and there is no
rem way to re-run build_aliases.py against a file sealed in an archive.
if exist "dist\emiten.json" (
  echo Kept the existing dist\emiten.json
) else (
  copy /y emiten.json dist\emiten.json >nul
  echo Seeded dist\emiten.json
)

rem Do NOT clobber a config already tuned inside dist\. The exe reads the
rem config next to ITSELF; copying over it is how you end up with two watchers
rem alerting differently.
if exist "dist\config.json" (
  echo Kept your existing dist\config.json
) else (
  copy /y config.json dist\config.json >nul
  echo Seeded dist\config.json from this folder
)
if not exist "dist\aliases_manual.json" copy /y aliases_manual.json dist\ >nul
echo.
echo ============================================================
echo  Built: %~dp0dist\JCIAlert.exe
echo.
echo  The exe reads config.json, emiten.json, seen.json,
echo  news.csv and logs\ from ITS OWN folder, not from this
echo  source folder. Editing Options in the exe does NOT change
echo  the source config, and vice versa. Move the whole dist
echo  folder if you relocate it.
echo.
echo  It runs alongside IDXAlert3 - separate tray icon, separate
echo  Run key, separate mutex. Neither can disturb the other.
echo.
echo  First run from its final location:
echo    - right-click the tray icon ^> Verify sources
echo    - then Options ^> Advanced ^> Start with Windows
echo      (so autostart points at the right path)
echo ============================================================
pause
exit /b 0

:fail
echo.
echo BUILD FAILED - see the error above. Nothing was built.
pause
exit /b 1
