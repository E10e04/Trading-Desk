"""Configuration de l'application (variables d'environnement + .env).

La clé OpenRouter n'est jamais exposée par l'API : elle est lue côté backend uniquement
(variable d'environnement ou fichier `data/secrets.json` en mode 0600).
"""
from __future__ import annotations

import json
import logging
import os
import re
import secrets
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

from dotenv import load_dotenv

BACKEND_DIR = Path(__file__).resolve().parent.parent
PROJECT_DIR = BACKEND_DIR.parent

# Le .env est cherché à la racine du projet puis dans backend/ (sans écraser l'environnement réel).
load_dotenv(PROJECT_DIR / ".env", override=False)
load_dotenv(BACKEND_DIR / ".env", override=False)

APP_NAME = "Trading Desk"
APP_VERSION = os.environ.get("APP_VERSION", "").strip() or "0.1.0"  # version de l'application installée (l'appli de bureau la fournit)
VENDOR_COMMIT = "3d0f4ef6986f4984b7c6bd46f7ff4f87744c3abd"
VENDOR_REPO = "https://github.com/Jakeschincariol/day-trader-skill"
VENDOR_VERSION = "2.0.0"
DEFAULT_MODEL = "minimax/minimax-m3.1-flash"

_SECRET_PATTERNS = [re.compile(r"sk-or-[A-Za-z0-9_\-]{8,}"), re.compile(r"(?i)(bearer\s+)[A-Za-z0-9._\-]{16,}")]


def _env(name: str, default: str = "") -> str:
    v = os.environ.get(name)
    return default if v is None or v.strip() == "" else v.strip()


@dataclass(frozen=True)
class Settings:
    data_dir: Path
    db_url: str
    host: str
    port: int
    cors_origins: tuple[str, ...]
    api_token: str
    biquote_base_url: str
    biquote_hub_path: str
    biquote_api_key: str
    openrouter_base_url: str
    openrouter_default_model: str
    sec_user_agent: str
    frontend_dist: Path | None = None
    extra: dict = field(default_factory=dict)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    data_dir = Path(_env("TRADING_DATA_DIR", str(BACKEND_DIR / "data"))).expanduser()
    if not data_dir.is_absolute():
        data_dir = (BACKEND_DIR / data_dir).resolve()
    data_dir.mkdir(parents=True, exist_ok=True)
    db_url = _env("TRADING_DB_URL", f"sqlite:///{data_dir / 'trading.sqlite'}")
    if db_url.startswith("sqlite:///./"):
        db_url = f"sqlite:///{(BACKEND_DIR / db_url[len('sqlite:///./'):]).resolve()}"
    token = _env("API_TOKEN") or _load_or_create_token(data_dir)
    dist = Path(_env("FRONTEND_DIST", str(PROJECT_DIR / "frontend" / "dist")))
    return Settings(
        data_dir=data_dir,
        db_url=db_url,
        host=_env("BACKEND_HOST", "127.0.0.1"),
        port=int(_env("BACKEND_PORT", "8787")),
        cors_origins=tuple(o.strip() for o in _env("CORS_ORIGINS", "http://localhost:5173,http://127.0.0.1:5173").split(",") if o.strip()),
        api_token=token,
        biquote_base_url=_env("BIQUOTE_BASE_URL", "https://biquote.io").rstrip("/"),
        biquote_hub_path=_env("BIQUOTE_HUB_PATH", "/hubs/tick"),
        biquote_api_key=_env("BIQUOTE_API_KEY"),
        openrouter_base_url=_env("OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1").rstrip("/"),
        openrouter_default_model=_env("OPENROUTER_DEFAULT_MODEL", DEFAULT_MODEL),
        sec_user_agent=_env("SEC_USER_AGENT"),
        frontend_dist=dist if dist.exists() else None,
    )


def reset_settings_cache() -> None:  # utilisé par les tests
    get_settings.cache_clear()


def _load_or_create_token(data_dir: Path) -> str:
    """Jeton local persistant (pour que l'interface reste connectée après un redémarrage)."""
    p = data_dir / "api_token.txt"
    if p.exists():
        t = p.read_text().strip()
        if t:
            return t
    t = secrets.token_urlsafe(32)
    p.write_text(t)
    try:
        os.chmod(p, 0o600)
    except OSError:
        pass
    return t


# ----------------------------------------------------------------------------- secrets


class SecretStore:
    """Stocke les secrets modifiables depuis l'interface (clé OpenRouter, UA SEC).

    Priorité : fichier `data/secrets.json` (réglé via l'UI) > variable d'environnement.
    Les valeurs ne sont jamais renvoyées par l'API (seulement un masque).
    """

    def __init__(self, data_dir: Path | None = None):
        self._path = (data_dir or get_settings().data_dir) / "secrets.json"

    def _read(self) -> dict:
        try:
            return json.loads(self._path.read_text())
        except (OSError, ValueError):
            return {}

    def get(self, name: str) -> str:
        v = self._read().get(name)
        if v:
            return str(v)
        return {"openrouter_api_key": _env("OPENROUTER_API_KEY"), "sec_user_agent": get_settings().sec_user_agent}.get(name, "")

    def set(self, name: str, value: str) -> None:
        d = self._read()
        if value:
            d[name] = value
        else:
            d.pop(name, None)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self._path.with_suffix(".tmp")
        tmp.write_text(json.dumps(d))
        try:
            os.chmod(tmp, 0o600)
        except OSError:
            pass
        tmp.replace(self._path)
        install_redaction()  # la nouvelle valeur doit être masquée dans les logs

    def source(self, name: str) -> str:
        if self._read().get(name):
            return "interface"
        return "environnement" if self.get(name) else "absente"

    def secret_values(self) -> list[str]:
        vals = [self.get("openrouter_api_key"), get_settings().api_token, get_settings().biquote_api_key]
        return [v for v in vals if v and len(v) >= 8]


def mask(value: str) -> str:
    if not value:
        return ""
    if len(value) <= 10:
        return "•" * len(value)
    return f"{value[:6]}…{value[-2:]}"


class _RedactFilter(logging.Filter):
    """Retire toute clé API des messages de log."""

    def __init__(self, store: SecretStore):
        super().__init__()
        self._store = store

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            msg = record.getMessage()
        except Exception:  # message mal formé: ne jamais casser le logging
            return True
        red = redact(msg, self._store.secret_values())
        if red != msg:
            record.msg, record.args = red, ()
        return True


def redact(text: str, extra_values: list[str] | tuple[str, ...] = ()) -> str:
    for v in extra_values:
        if v:
            text = text.replace(v, "***")
    for pat in _SECRET_PATTERNS:
        text = pat.sub(lambda m: (m.group(1) if m.lastindex else "") + "***", text)
    return text


_installed = False


def install_redaction() -> None:
    global _installed
    root = logging.getLogger()
    for h in root.handlers:
        if not any(isinstance(f, _RedactFilter) for f in h.filters):
            h.addFilter(_RedactFilter(SecretStore()))
    _installed = True


def setup_logging(level: int = logging.INFO) -> None:
    logging.basicConfig(level=level, format="%(asctime)s %(levelname)-7s %(name)s: %(message)s")
    install_redaction()
