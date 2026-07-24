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
if [ ! -f webapp/price_cache.pkl ]; then
  touch webapp/price_cache.pkl  # data.py loads a missing/empty/corrupt file as a cold cache
fi
if [ ! -f webapp/computed_cache.pkl ]; then
  touch webapp/computed_cache.pkl  # app.py loads a missing/empty/corrupt file as a cold cache
fi
if [ ! -f webapp/universe_last_screened.txt ]; then
  touch webapp/universe_last_screened.txt  # missing/empty means "never screened", forces one
fi

docker compose up -d --build

# Cloudflare Tunnel can hold a stale connection to the old container after a
# rebuild -- restart it so it re-establishes against the fresh one.
sudo systemctl restart cloudflared
