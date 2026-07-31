"""Package bootstrap."""

# Must run before any yfinance-touching import below, including build_universe.
from backend import yf_cache_patch
yf_cache_patch.apply()
