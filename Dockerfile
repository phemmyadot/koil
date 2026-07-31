FROM node:20-slim AS frontend-build
WORKDIR /frontend
COPY frontend/package.json frontend/package-lock.json* ./
RUN npm install
COPY frontend/ ./
RUN npm run build

FROM python:3.12-slim

WORKDIR /app

RUN apt-get update && apt-get install -y \
    gcc \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .
# React SPA build output. Not yet mounted by app.py (which still serves
# backend/static/, the legacy frontend) -- lands here so it's ready for the
# StaticFiles mount to switch over once the SPA is feature-complete. See
# docs/frontend-docker-setup.md.
COPY --from=frontend-build /frontend/dist ./backend/static_frontend

EXPOSE 3006

CMD ["uvicorn", "backend.app:app", "--host", "0.0.0.0", "--port", "3006"]
