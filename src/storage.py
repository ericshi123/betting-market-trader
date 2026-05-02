import json
import os
from datetime import date, datetime
from pathlib import Path

SNAPSHOTS_DIR = Path(__file__).parent.parent / "data" / "snapshots"
ANALYSIS_DIR = Path(__file__).parent.parent / "data" / "analysis"


def _ensure_dir():
    SNAPSHOTS_DIR.mkdir(parents=True, exist_ok=True)


def _ensure_analysis_dir():
    ANALYSIS_DIR.mkdir(parents=True, exist_ok=True)


def save_snapshot(markets: list[dict]) -> Path:
    _ensure_dir()
    today = date.today().isoformat()
    path = SNAPSHOTS_DIR / f"{today}.json"

    # Strip _raw to keep files readable
    clean = [{k: v for k, v in m.items() if k != "_raw"} for m in markets]
    with open(path, "w") as f:
        json.dump({"date": today, "count": len(clean), "markets": clean}, f, indent=2)

    return path


def load_latest_snapshot() -> list[dict] | None:
    _ensure_dir()
    files = sorted(SNAPSHOTS_DIR.glob("*.json"), reverse=True)
    if not files:
        return None
    with open(files[0]) as f:
        data = json.load(f)
    return data.get("markets", [])


def save_analysis(markets: list[dict]) -> Path:
    _ensure_analysis_dir()
    stamp = datetime.utcnow().strftime("%Y-%m-%d-%H")
    path = ANALYSIS_DIR / f"{stamp}.json"

    _STRIP = {"_raw"}
    clean = [{k: v for k, v in m.items() if k not in _STRIP} for m in markets]
    with open(path, "w") as f:
        json.dump(
            {"timestamp": datetime.utcnow().isoformat(), "count": len(clean), "markets": clean},
            f,
            indent=2,
        )
    return path


def load_latest_analysis() -> list[dict] | None:
    _ensure_analysis_dir()
    files = sorted(ANALYSIS_DIR.glob("*.json"), reverse=True)
    if not files:
        return None
    with open(files[0]) as f:
        data = json.load(f)
    return data.get("markets", [])
