@echo off
setlocal
cd /d "%~dp0"

if "%SHRECKNET_OLLAMA_GPU_MODE%"=="" set "SHRECKNET_OLLAMA_GPU_MODE=auto"

set "COMPOSE_FILES=-f docker-compose.yml"
set "ENABLE_GPU=false"

if /I "%SHRECKNET_OLLAMA_GPU_MODE%"=="on" set "ENABLE_GPU=true"
if /I "%SHRECKNET_OLLAMA_GPU_MODE%"=="off" set "ENABLE_GPU=false"

if /I "%SHRECKNET_OLLAMA_GPU_MODE%"=="auto" (
	where nvidia-smi >nul 2>&1
	if not errorlevel 1 (
		nvidia-smi -L >nul 2>&1
		if not errorlevel 1 set "ENABLE_GPU=true"
	)
)

if /I "%SHRECKNET_OLLAMA_GPU_MODE%"=="on" goto :mode_ok
if /I "%SHRECKNET_OLLAMA_GPU_MODE%"=="off" goto :mode_ok
if /I "%SHRECKNET_OLLAMA_GPU_MODE%"=="auto" goto :mode_ok
echo [run.bat] Invalid SHRECKNET_OLLAMA_GPU_MODE="%SHRECKNET_OLLAMA_GPU_MODE%". Use: auto ^| on ^| off
exit /b 1

:mode_ok
if /I "%ENABLE_GPU%"=="true" (
	set "COMPOSE_FILES=%COMPOSE_FILES% -f docker-compose.gpu.yml"
	echo [run.bat] Ollama GPU mode enabled (mode=%SHRECKNET_OLLAMA_GPU_MODE%)
) else (
	echo [run.bat] Ollama CPU mode enabled (mode=%SHRECKNET_OLLAMA_GPU_MODE%)
)

docker compose --env-file configs/compose.env --env-file configs/neo4j.env %COMPOSE_FILES% up --build
