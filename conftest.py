"""Root conftest: ensure the repo root is first on sys.path so that
'card_summary' resolves to the top-level package."""
import sys
from pathlib import Path

_repo_root = str(Path(__file__).parent)

while _repo_root in sys.path:
    sys.path.remove(_repo_root)
sys.path.insert(0, _repo_root)
