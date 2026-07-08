@echo off
setlocal
cd /d "%~dp0"

docker compose --env-file configs/compose.env --env-file configs/neo4j.env -f docker-compose.yml up --build
