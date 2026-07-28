"""Package bootstrap.

webapp/tickers.py is gitignored (generated, per-environment) -- a fresh
checkout or Docker image never has it. Every submodule that touches TICKERS
(data.py, app.py) imports it at module load time, so without this bootstrap
the container crash-loops on ModuleNotFoundError before it ever gets a
chance to run build_universe.py. Runs once, here, before any submodule
import, so the app self-heals on first boot instead of requiring someone to
SSH in and run the CLI manually.
"""
import os

# Must run before ANY yfinance-touching import below (build_universe included) -- see
# webapp/yf_cache_patch.py's docstring for why yfinance's on-disk caches are disabled
# entirely. This is the earliest point in the whole app's import graph, since a cold
# container hits build_universe (imported just below) before data.py or p.py ever load.
from webapp import yf_cache_patch
yf_cache_patch.apply()

_TICKERS_PATH = os.path.join(os.path.dirname(__file__), "tickers.py")

if not os.path.isfile(_TICKERS_PATH):
    try:
        from webapp import build_universe
        candidates = build_universe.fetch_candidates()
        passed = build_universe.screen_technicals(candidates)
        build_universe.write_tickers_file(passed)
    except Exception as e:  # noqa: BLE001 - must not crash-loop the container
        print(f"webapp: initial tickers.py bootstrap failed ({e}); "
              f"starting with an empty universe until the next refresh.")
        with open(_TICKERS_PATH, "w") as f:
            f.write('"""Bootstrap placeholder -- see webapp/__init__.py."""\n\nTICKERS = []\n')
