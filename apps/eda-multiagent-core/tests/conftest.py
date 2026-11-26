import sys
from pathlib import Path

# Stellt sicher, dass `core.*` aus dem Projektroot importierbar ist,
# egal von wo pytest aufgerufen wird.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
