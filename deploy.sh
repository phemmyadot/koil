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
if [ ! -f webapp/universe_screen_cache.json ]; then
  echo '{}' > webapp/universe_screen_cache.json
fi

docker compose up -d --build
