import sys
from pathlib import Path

# Ensure project root is on sys.path so `src.*` imports resolve when running pytest
sys.path.insert(0, str(Path(__file__).parent))
