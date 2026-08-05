#!/bin/bash
set -e
cd frontend && npm run build && cd ..
rm -rf backend/static_frontend && cp -r frontend/dist backend/static_frontend
.venv/Scripts/python.exe -m uvicorn backend.app:app --port 8123
