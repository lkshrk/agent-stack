import sys
from pathlib import Path

POLICIES = str(Path(__file__).resolve().parents[2] / "policies" / "omnigent")
if POLICIES not in sys.path:
    sys.path.insert(0, POLICIES)
