# Running the frontend in the backend's container

The React SPA (`frontend/`) is built and shipped inside the same Docker image as the
FastAPI backend (`backend/`) -- one container, one deploy, no separate frontend host.

## How the build works

`Dockerfile` is a two-stage build:

1. **`frontend-build` stage** (`node:20-slim`): installs `frontend/`'s npm dependencies
   and runs `npm run build` (`tsc -b && vite build`), producing a static bundle in
   `frontend/dist/`.
2. **Final stage** (`python:3.12-slim`): installs Python deps, copies the repo in, then
   copies the built bundle from the first stage into `backend/static_frontend/`.

No Node is required at runtime -- the final image only has Python. No Node needs to be
installed on the deploy host either; `docker build` runs the Node stage inside its own
throwaway build container.

## Wiring it into FastAPI

`backend/app.py` currently mounts `backend/static/` (the legacy hand-written HTML
frontend) at `/` via `StaticFiles`. `backend/static_frontend/` (the new SPA build
output) is copied into the image but **not yet mounted** -- the SPA is still mid-build
(see `docs/superpowers/specs/2026-07-31-react-spa-rewrite-design.md`) and isn't ready to
replace the page users currently see in production.

To cut over once the SPA is feature-complete, change the mount in `backend/app.py`:

```python
app.mount("/", StaticFiles(directory=os.path.join(os.path.dirname(__file__), "static_frontend"),
                           html=True), name="static")
```

and delete `backend/static/` (the legacy frontend) once nothing references it.

## Local development

The SPA is not run through Docker/uvicorn's static mount during development -- use
Vite's own dev server, which proxies API calls to a locally-running backend:

```bash
# terminal 1 -- backend
.venv/Scripts/python.exe -m uvicorn backend.app:app --port 8123

# terminal 2 -- frontend (proxies /api to :8123, see frontend/vite.config.ts)
cd frontend && npm run dev
```

## Deploy

No changes needed to `deploy.sh` beyond what already exists -- `docker compose up -d
--build` builds both stages as part of the normal image build. There is no separate
frontend deploy step.
