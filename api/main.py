"""Project-root entrypoint for uvicorn: `uvicorn api.main:app`.

This thin shim exists because the master spec (00_MASTER §5) calls
`uvicorn api.main:app --port 8000` from the project root. The actual
FastAPI app is in `src/api/main.py`; this file re-exports it under the
name `app` so uvicorn can find it on `sys.path` rooted at the project.
"""
import sys
from pathlib import Path

# Make `src` importable
_ROOT = Path(__file__).resolve().parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from src.api.main import app, create_app  # noqa: E402, F401

__all__ = ["app", "create_app"]


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("api.main:app", host="0.0.0.0", port=8000, reload=False)
