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

`backend/app.py` mounts `backend/static_frontend/` (the built SPA) at `/` via a custom
`SPAStaticFiles` class -- a thin `StaticFiles` subclass that falls back to serving
`index.html` for any GET that doesn't match a real file. That fallback matters because
react-router's `browserRouter` (not hash-based) needs it: a hard refresh or a direct link
to e.g. `/trades/42` is a real GET to the server, not a client-side navigation, and plain
`StaticFiles` would 404 that instead of loading the app shell that handles the route
client-side.

`backend/static/` (the old hand-written HTML frontend) is **kept in the repo for
reference only** -- not mounted, not served at any path, not deleted. There is no
`/legacy` route or similar; any request that doesn't match a real SPA asset falls
straight through to the SPA's own `index.html`, same as every other unmatched route.

The app now **requires** `backend/static_frontend/` to exist -- `app.py` fails to import
if it's missing, since there's no other frontend to fall back to. This matches Docker's
build (the `frontend-build` stage always produces it before the Python stage starts),
but means a bare `uvicorn backend.app:app` outside Docker needs the SPA built first (see
Local development below).

## Local development

Two options:

- **Fastest iteration**: run the SPA through Vite's own dev server, which proxies API
  calls to a locally-running backend and hot-reloads on save.

  ```bash
  # terminal 1 -- backend
  .venv/Scripts/python.exe -m uvicorn backend.app:app --port 8123

  # terminal 2 -- frontend (proxies /api to :8123, see frontend/vite.config.ts)
  cd frontend && npm run dev
  ```

  Note `backend/app.py` still needs `backend/static_frontend/` to exist to import at all
  (see above) even though this path doesn't serve it -- run `npm run build` once first if
  you haven't, or copy `frontend/dist/*` into `backend/static_frontend/`.

- **Testing the built SPA through the backend directly** (closer to production):

  ```bash
  cd frontend && npm run build
  # copy frontend/dist/* into backend/static_frontend/, then:
  .venv/Scripts/python.exe -m uvicorn backend.app:app --port 8123
  ```

## Deploy

No changes needed to `deploy.sh` beyond what already exists -- `docker compose up -d
--build` builds both stages as part of the normal image build. There is no separate
frontend deploy step.
