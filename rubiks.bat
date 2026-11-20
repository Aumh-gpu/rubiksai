@echo off
setlocal
rem RUBIKS launcher. Double-click for chat, or use it from any terminal:
rem   rubiks            chat          rubiks serve --lan   phone access
rem   rubiks ask "..."  one answer    rubiks voice         original voice assistant

if not exist "%~dp0.installed" (
  echo Installing requirements, one time only...
  python -m pip install -r "%~dp0requirements.txt"
  python -m playwright install chromium
  echo ok> "%~dp0.installed"
)

python "%~dp0rubiks_cli.py" %*
exit /b %ERRORLEVEL%
