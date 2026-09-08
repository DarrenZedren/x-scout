from __future__ import annotations

import argparse
from pathlib import Path

from x_scout.bridge import ScoutBridge
from x_scout.config import load_config
from x_scout.database import ScoutDatabase
from x_scout.engine import ScoutEngine
from x_scout.providers import MockProvider

ROOT = Path(__file__).resolve().parent


def mission_config(db: ScoutDatabase) -> dict:
    cfg = load_config()
    p = db.active_profile()
    cfg["profile_name"] = p.name
    cfg["wanted_terms"] = p.wanted_terms
    cfg["blocked_terms"] = p.blocked_terms
    cfg["activity_terms"] = p.activity_terms
    cfg["query_groups"] = p.query_groups
    return cfg


def headless_demo() -> int:
    db = ScoutDatabase()
    config = mission_config(db)
    engine = ScoutEngine(MockProvider(config, seed=42), config, db)
    people, conversations = engine.scan("demo-headless", lambda pct, msg: print(f"[{pct:3d}%] {msg}"))
    print(f"\nPASS: {len(people)} people, {len(conversations)} conversations // profile={config['profile_name']}")
    return 0 if people and conversations else 1


def main() -> int:
    parser = argparse.ArgumentParser(description="X Scout local flight-deck utility")
    parser.add_argument("--headless-demo", action="store_true")
    args = parser.parse_args()
    if args.headless_demo:
        return headless_demo()

    try:
        import webview
    except ImportError:
        print("X Scout v0.4.0 needs its local desktop UI package.")
        print("Install it once with:")
        print("  python -m pip install -r requirements.txt")
        return 2

    bridge = ScoutBridge()
    index = (ROOT / "web" / "index.html").resolve().as_uri()
    webview.create_window(
        "X Scout // Flight Deck",
        index,
        js_api=bridge,
        width=1500,
        height=930,
        min_size=(1060, 720),
        background_color="#02050a",
    )
    webview.start(debug=False)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
