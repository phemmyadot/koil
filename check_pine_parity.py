"""Config-drift check between backend/crypto_v2/config.py's CONFIG and pines/crypto_v2.pine's
own copy of the same constants.

Pine can't import Python, so every load-bearing constant is hand-duplicated in the .pine file,
tagged with a "// PARITY: <dotted.path>" comment on the line directly above its assignment. This
script extracts every tagged value, resolves the dotted path against the live CONFIG dict, and
asserts they still match -- catching exactly the kind of silent drift that left
pines/vcp_crypto.pine's bucket constants stale for weeks after crypto_universe.py's meme-bucket
retune (found and fixed in this repo's history; there was no check like this one for v1).

Usage: .venv/bin/python check_pine_parity.py
Exits 0 if every tagged constant matches, 1 (with a per-constant report) if any diverge.
"""
import re
import sys
from pathlib import Path

from backend.crypto_v2.config import CONFIG

PINE_FILE = Path(__file__).parent / "pines" / "crypto_v2.pine"

# "// PARITY: <dotted.path>" on one line, then the next line's "... = <number>" assignment.
_TAG_RE = re.compile(
    r"^\s*//\s*PARITY:\s*(?P<path>[\w.]+)\s*$\n"
    r"^\s*(?:int|float|bool)\s+\w+\s*=\s*(?P<value>-?\d+\.?\d*)\s*$",
    re.MULTILINE,
)


def resolve(path: str):
    node = CONFIG
    for part in path.split("."):
        if not isinstance(node, dict) or part not in node:
            raise KeyError(f"{path!r} not found in CONFIG (missing at {part!r})")
        node = node[part]
    return node


def main() -> int:
    text = PINE_FILE.read_text()
    matches = list(_TAG_RE.finditer(text))
    if not matches:
        print(f"No // PARITY: tags found in {PINE_FILE} -- nothing to check.")
        return 1

    failures = []
    for m in matches:
        path, pine_value_str = m.group("path"), m.group("value")
        pine_value = float(pine_value_str)
        try:
            python_value = float(resolve(path))
        except KeyError as e:
            print(f"FAIL  {path}: {e}")
            failures.append(path)
            continue
        if pine_value == python_value:
            print(f"PASS  {path}: {python_value}")
        else:
            print(f"FAIL  {path}: pine={pine_value} python={python_value}")
            failures.append(path)

    print(f"\n{len(matches) - len(failures)}/{len(matches)} constants match.")
    if failures:
        print(f"Mismatched: {', '.join(failures)}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
