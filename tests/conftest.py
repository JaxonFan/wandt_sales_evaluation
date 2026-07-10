"""Make `builders` and the `app` package importable regardless of pytest's cwd."""
import os
import sys

_HERE = os.path.dirname(__file__)
_ROOT = os.path.dirname(_HERE)
for p in (_HERE, _ROOT):
    if p not in sys.path:
        sys.path.insert(0, p)
