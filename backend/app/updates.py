"""Vérification des mises à jour : compare la version installée à la dernière release publiée sur GitHub.

Lecture seule de l'API publique GitHub (aucun jeton). Le téléchargement et l'installation sont faits par l'application de bureau
(voir desktop/src/updater.js) ; en mode web, l'interface renvoie simplement vers la page de la release.
"""
from __future__ import annotations

import re
import time
from typing import Any

import httpx

from .config import APP_VERSION

DEFAULT_REPO = "E10e04/Trading-Desk"
_TTL = 600
_cache: dict[str, Any] = {"at": 0.0, "repo": "", "data": None}
_V = re.compile(r"^v?(\d+)\.(\d+)\.(\d+)(?:[-+].*)?$")


def parse_version(tag: str) -> tuple[int, int, int] | None:
    m = _V.match((tag or "").strip())
    return (int(m[1]), int(m[2]), int(m[3])) if m else None


def _asset_kind(name: str) -> str | None:
    n = name.lower()
    if n.endswith(".exe"):
        return "win"
    if n.endswith(".dmg"):
        return "mac"
    return None


def summarize(releases: list[dict], current: str) -> dict[str, Any]:
    items = []
    for r in releases:
        if r.get("draft") or r.get("prerelease"):
            continue
        v = parse_version(r.get("tag_name", ""))
        if v is None:
            continue
        assets = [{"name": a["name"], "url": a["browser_download_url"], "size": a.get("size"), "sha256": (a.get("digest") or "").removeprefix("sha256:") or None,
                   "platform": _asset_kind(a["name"])} for a in r.get("assets", []) if _asset_kind(a.get("name", ""))]
        items.append({"version": ".".join(map(str, v)), "tag": r["tag_name"], "name": r.get("name") or r["tag_name"], "published_at": r.get("published_at"),
                      "notes": (r.get("body") or "")[:2000], "url": r.get("html_url"), "assets": assets, "_key": v})
    items.sort(key=lambda x: x["_key"], reverse=True)
    for i in items:
        i.pop("_key")
    latest = items[0] if items else None
    return {"current": current, "latest": latest, "releases": items[:15],
            # « différente de la version installée » : le critère demandé est l'inégalité
            "update_available": bool(latest and latest["version"] != current)}


async def check(repo: str | None = None, current: str = APP_VERSION, force: bool = False, client: httpx.AsyncClient | None = None) -> dict[str, Any]:
    repo = repo or DEFAULT_REPO
    if not force and _cache["data"] is not None and _cache["repo"] == repo and time.monotonic() - _cache["at"] < _TTL:
        return {**_cache["data"], "current": current, "update_available": bool(_cache["data"]["latest"] and _cache["data"]["latest"]["version"] != current)}
    own = client is None
    c = client or httpx.AsyncClient(timeout=10, headers={"Accept": "application/vnd.github+json", "User-Agent": "trading-desk-update-check"})
    try:
        r = await c.get(f"https://api.github.com/repos/{repo}/releases", params={"per_page": 30})
        if r.status_code == 404:
            raise RuntimeError("dépôt ou releases introuvables")
        if r.status_code in (403, 429):
            raise RuntimeError("limite de requêtes GitHub atteinte, réessayez plus tard")
        r.raise_for_status()
        data = summarize(r.json(), current)
    finally:
        if own:
            await c.aclose()
    _cache.update(at=time.monotonic(), repo=repo, data=data)
    return data
