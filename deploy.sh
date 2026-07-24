#!/bin/bash
set -e
cd ~/pine-trend-strategy
git pull origin master

# Bind-mounted as files in docker-compose.yml -- if they don't exist yet on
# the host, Docker creates them as directories instead of files, which
# breaks the webapp/__init__.py bootstrap. Ensure both exist as real files
# before compose ever touches them (empty tickers.py must still be valid
# Python, not just any empty file, so it's written properly, not touch'd).
if [ ! -f webapp/tickers.py ]; then
  printf '"""Bootstrap placeholder."""\n\nTICKERS = []\n' > webapp/tickers.py
fi
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
