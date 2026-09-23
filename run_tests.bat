@echo off
REM JCIAlert test suites. All offline - no network, no display, no IDX.
REM
REM test_jcisource.py and test_jciengine.py run against fixtures/ - real bytes
REM captured from these sites. If they are missing the suites skip and say so;
REM recapture with:  python probe_sources.py --save-fixtures fixtures
REM
REM test_aliases.py also statically checks every module for undefined names and
REM duplicate definitions, because the network paths cannot be exercised where
REM the code is edited.
python test_aliases.py
if errorlevel 1 exit /b 1
echo.
python test_jcimatch.py
if errorlevel 1 exit /b 1
echo.
python test_jcifilter.py
if errorlevel 1 exit /b 1
echo.
python test_jcisource.py
if errorlevel 1 exit /b 1
echo.
python test_jciengine.py
if errorlevel 1 exit /b 1
echo.
python test_jcinet.py
if errorlevel 1 exit /b 1
echo.
python test_jciview.py
if errorlevel 1 exit /b 1
echo.
python test_jcioptions.py
if errorlevel 1 exit /b 1
echo.
python test_jci.py
if errorlevel 1 exit /b 1
echo.
python test_jcitray.py
if errorlevel 1 exit /b 1
echo.
python test_jciwindow.py
if errorlevel 1 exit /b 1
echo.
python test_jcidash.py
if errorlevel 1 exit /b 1
echo.
python test_jcidashwindow.py
if errorlevel 1 exit /b 1
echo.
python test_jcidoctor.py
if errorlevel 1 exit /b 1
echo.
python test_installer.py
if errorlevel 1 exit /b 1
echo.
if not exist "fixtures\*.xml" (
  echo.
  echo ------------------------------------------------------------------
  echo  All suites passed, but fixtures\ is EMPTY, so the parsers and the
  echo  end-to-end chain were not tested - only the logic that needs no
  echo  bytes. fixtures\ is captured third-party pages and is not in the
  echo  repo. Before trusting a green run on a fresh clone:
  echo      python probe_sources.py --save-fixtures fixtures
  echo ------------------------------------------------------------------
  exit /b 0
)
echo All JCIAlert suites passed.
