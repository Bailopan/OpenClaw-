from __future__ import annotations

import asyncio
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from supplier_radar.main import run


def handler(event, context):
    """Yandex Cloud Functions entrypoint for Timer/manual invocations."""
    result = asyncio.run(run())
    return {
        "ok": result["summary"].get("status") in {"OK", "PARTIAL"},
        "source": (event or {}).get("source") if isinstance(event, dict) else None,
        "summary": result["summary"],
    }
