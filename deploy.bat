@echo off
rem Publish a GitHub release:  deploy 0.1.12
rem Runs the tests, then packaging\bump.py: sets __version__, commits "Release X",
rem tags vX and pushes main + the tag. The tag starts the GitHub workflow, which
rem builds the installers and publishes the release.
setlocal
cd /d "%~dp0"
if "%~1"=="" (
    echo usage: deploy ^<version^>   e.g. deploy 0.1.12
    exit /b 1
)
if not exist ".venv\Scripts\python.exe" (
    echo No .venv here. Run run.bat once first.
    exit /b 1
)
rem never publish a file that holds a GitHub token
findstr /s /m /c:"github_pat_" /c:"ghp_" *.md *.py *.js *.txt *.yml >nul 2>&1
if not errorlevel 1 (
    echo A GitHub token is in these files; remove it before releasing:
    findstr /s /m /c:"github_pat_" /c:"ghp_" *.md *.py *.js *.txt *.yml
    exit /b 1
)
echo Running tests...
".venv\Scripts\python.exe" -m pytest tests -q || (echo Tests failed, nothing was released. & exit /b 1)
".venv\Scripts\python.exe" packaging\bump.py %* || exit /b 1
echo.
echo Pushed v%~1. Follow the build at: https://github.com/pyhttp2/pyhttp/actions
