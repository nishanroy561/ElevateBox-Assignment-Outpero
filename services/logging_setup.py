"""Central logging setup.

Called from every process entrypoint (agent/worker.py, api/main.py). Adds a
`FileHandler` on top of the stream handler so `output.txt` at the project
root always has the latest logs from that process, ready to paste back
into a debugging chat.

Behavior:
- File: appends to <project-root>/output.txt in mode='a', so multiple
  processes running side-by-side (worker + uvicorn) both write to the
  same file. Restarting a process does NOT truncate (multiple test calls
  in one session all end up together).
- Console: stays intact, unaffected.
- Level: root logger set to INFO by default; override with LOG_LEVEL env.
- Skipping: set NO_FILE_LOG=1 in env to disable file logging (e.g. prod
  container where stdout gets shipped elsewhere).
"""
from __future__ import annotations

import logging
import os
from pathlib import Path

_LOG_FILE = Path(__file__).resolve().parent.parent / "output.txt"

_LOG_FORMAT = "%(asctime)s %(levelname)s %(name)s: %(message)s"
_configured = False


def setup_logging() -> None:
    """Idempotent -- safe to call from every entrypoint."""
    global _configured
    if _configured:
        return

    level = os.getenv("LOG_LEVEL", "INFO").upper()
    root = logging.getLogger()
    root.setLevel(level)

    # Stream handler (stderr/stdout) -- always on. Only add one if none
    # exists yet (basicConfig may have added one).
    if not any(isinstance(h, logging.StreamHandler) and not isinstance(h, logging.FileHandler)
               for h in root.handlers):
        sh = logging.StreamHandler()
        sh.setFormatter(logging.Formatter(_LOG_FORMAT))
        root.addHandler(sh)

    # File handler -- unless explicitly disabled.
    if os.getenv("NO_FILE_LOG") != "1":
        fh = logging.FileHandler(_LOG_FILE, mode="a", encoding="utf-8")
        fh.setFormatter(logging.Formatter(_LOG_FORMAT))
        root.addHandler(fh)

    _configured = True
