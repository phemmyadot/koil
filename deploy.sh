#!/bin/bash
set -e
cd ~/pine-trend-strategy
git pull origin master

# deploy.sh can change in the same pull that just ran (as it just did) --
# bash reads a running script incrementally, so continuing to execute THIS
# process after git pull rewrote the file on disk risks a mix of pre-pull
# and post-pull lines (exactly what caused a stale reference to a since-
# renamed cache file to run here once). Re-exec a guaranteed-fresh copy of
# the post-pull script instead of trusting the rest of this file. Guarded by
# an env var so the re-exec'd process doesn't pull+re-exec forever.
if [ -z "$DEPLOY_SH_REEXECED" ]; then
  export DEPLOY_SH_REEXECED=1
  exec bash "$0" "$@"
fi

# Bind-mounted as a file in docker-compose.yml -- if it doesn't exist yet on
# the host, Docker creates it as a directory instead of a file, which breaks
# the db.py schema init. Ensure it exists as a real file before compose ever
# touches it.
if [ ! -f webapp/app_data.db ]; then
  touch webapp/app_data.db  # db.py creates the schema fresh in an empty file
fi
if [ ! -f webapp/app_data.db-wal ]; then
  touch webapp/app_data.db-wal  # SQLite WAL sidecar -- must exist as a file, not get
fi
if [ ! -f webapp/app_data.db-shm ]; then
  touch webapp/app_data.db-shm  # auto-vivified as a directory by the bind mount
fi

docker compose up -d --build

# Cloudflare Tunnel can hold a stale connection to the old container after a
# rebuild -- restart it so it re-establishes against the fresh one.
sudo systemctl restart cloudflared
