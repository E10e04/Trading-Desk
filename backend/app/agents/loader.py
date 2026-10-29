"""Chargement des skills vendorisés (day-trader-skill, commit épinglé).

Les fichiers du dossier `vendor/` ne sont JAMAIS modifiés : chaque script est chargé comme module
privé via importlib. Les adaptations vivent dans `adapt.py` et `app/agents/*`.
"""
from __future__ import annotations

import importlib.util
import os
import threading
from functools import lru_cache
from pathlib import Path
from types import ModuleType

from ..config import VENDOR_COMMIT, VENDOR_REPO, VENDOR_VERSION

VENDOR_DIR = Path(__file__).resolve().parent.parent / "vendor" / "day_trader_skill"
_lock = threading.Lock()

# agent -> (dossier du skill, script principal)
SKILL_FILES = {
    "scanner": ("trader-scanner", "scan"),
    "news": ("trader-news", "news"),
    "entry": ("trader-entry", "entry"),
    "exit": ("trader-exit", "exit"),
    "timemachine": ("trader-timemachine", "timemachine"),
    "risk": ("trader-risk", "check"),
    "budget": ("trader-budget", "size"),
    "broker": ("trader-broker", "ticket"),
    "alerts": ("trader-alerts", "alerts"),
    "analyst": ("trader-analyst", "facts"),
    "pelosi": ("trader-pelosi", "ptr"),
}


def skill_version() -> str:
    return f"day-trader-skill {VENDOR_VERSION} @ {VENDOR_COMMIT[:7]}"


@lru_cache(maxsize=None)
def load_skill(agent_id: str) -> ModuleType:
    folder, name = SKILL_FILES[agent_id]
    path = VENDOR_DIR / folder / f"{name}.py"
    if not path.exists():
        raise FileNotFoundError(f"skill introuvable : {path}")
    with _lock:
        spec = importlib.util.spec_from_file_location(f"vendor_trader_{name}", path)
        mod = importlib.util.module_from_spec(spec)  # type: ignore[arg-type]
        spec.loader.exec_module(mod)  # type: ignore[union-attr]
    return mod


def skill_md(agent_id: str) -> str:
    folder, _ = SKILL_FILES[agent_id]
    return (VENDOR_DIR / folder / "SKILL.md").read_text(encoding="utf-8")


def provenance() -> dict:
    return {"repo": VENDOR_REPO, "commit": VENDOR_COMMIT, "version": VENDOR_VERSION, "license": "MIT",
            "copyright": "Copyright (c) 2026 Jake Schincariol", "path": str(VENDOR_DIR)}


def integrity() -> dict[str, bool]:
    """Vérifie que chaque skill se charge (utilisé par le diagnostic)."""
    out = {}
    for a in SKILL_FILES:
        try:
            load_skill(a)
            out[a] = True
        except Exception:
            out[a] = False
    return out
