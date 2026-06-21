@echo off
setlocal

set "ROOT=%~dp0"
cd /d "%ROOT%" || exit /b 1

echo [wechat_agent] Project: %CD%
echo.

where python >nul 2>nul
if errorlevel 1 (
  echo [error] Python was not found. Please install Python 3.10+ and add it to PATH.
  pause
  exit /b 1
)

where npm >nul 2>nul
if errorlevel 1 (
  echo [error] npm was not found. Please install Node.js and add it to PATH.
  pause
  exit /b 1
)

if not exist "%ROOT%.env" (
  if exist "%ROOT%.env.example" (
    copy "%ROOT%.env.example" "%ROOT%.env" >nul
    echo [setup] Created .env from .env.example.
    echo [setup] Edit .env later if the chat model is not configured yet.
    echo.
  )
)

if not exist "%ROOT%.venv\Scripts\python.exe" (
  echo [setup] Creating Python virtual environment...
  python -m venv "%ROOT%.venv"
  if errorlevel 1 (
    echo [error] Failed to create Python virtual environment.
    pause
    exit /b 1
  )
  echo.
)

set "PYTHON_EXE=%ROOT%.venv\Scripts\python.exe"

echo [setup] Checking backend dependencies...
"%PYTHON_EXE%" -c "import fastapi, uvicorn, dotenv, langchain_core, langchain_openai, openai, pydantic, sqlite_vec, multipart" >nul 2>nul
if errorlevel 1 (
  echo [setup] Installing backend dependencies...
  "%PYTHON_EXE%" -m pip install -r "%ROOT%requirements.txt"
  if errorlevel 1 (
    echo [error] Failed to install backend dependencies.
    pause
    exit /b 1
  )
)
echo.

if not exist "%ROOT%frontend\node_modules" (
  echo [setup] Installing frontend dependencies...
  pushd "%ROOT%frontend"
  call npm install
  if errorlevel 1 (
    popd
    echo [error] Failed to install frontend dependencies.
    pause
    exit /b 1
  )
  popd
  echo.
)

echo [start] Backend:  http://localhost:8000
start "wechat_agent backend" /D "%ROOT%" cmd /k ""%PYTHON_EXE%" -m uvicorn backend.main:app --reload --host 0.0.0.0 --port 8000"

echo [start] Frontend: http://localhost:5173
start "wechat_agent frontend" /D "%ROOT%frontend" cmd /k "npm run dev -- --host 127.0.0.1"

echo.
echo Both services are starting in separate windows.
echo Open http://localhost:5173 after Vite finishes compiling.
echo Close the backend/frontend windows to stop the project.
echo.

pause
endlocal
