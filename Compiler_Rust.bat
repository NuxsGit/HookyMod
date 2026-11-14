@echo off
setlocal
cd /d "%~dp0"
echo Ferme HookyCreator avant de compiler le moteur Rust.
echo.
where py >nul 2>nul
if %errorlevel%==0 (
  py -3 tools\build_rust.py
) else (
  python tools\build_rust.py
)
if errorlevel 1 (
  echo.
  echo La compilation a echoue. Lis le message ci-dessus.
) else (
  echo.
  echo Termine. Redemarre HookyCreator pour utiliser le moteur Rust.
)
pause
