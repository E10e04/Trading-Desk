"""Classification des instruments et paramètres de négociation par classe d'actifs.

BiQuote est un flux de CFD (MetaTrader 5) : forex, métaux/énergie, crypto, indices, actions.
La classe d'actifs détermine : la devise de cotation (pour convertir le P&L en USD), le pas de
quantité minimal (« lot entier » pour les skills Budget/Risk), le spread estimé si bid/ask manquent,
et le profil de session (NYSE ou marché continu).
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from .provider import Instrument

CURRENCIES = {
    "USD", "EUR", "GBP", "JPY", "CHF", "AUD", "NZD", "CAD", "SEK", "NOK", "DKK", "PLN", "CZK", "HUF", "TRY", "ZAR",
    "MXN", "THB", "SGD", "HKD", "CNH", "CNY", "ILS", "RON", "BRL", "INR", "KRW", "RUB", "AED", "SAR", "CLP", "COP",
    "IDR", "PHP", "TWD", "BGN", "HRK", "ISK", "RSD", "UAH", "KZT", "EGP", "NGN", "KES", "MAD",
}
METALS = {"XAU", "XAG", "XPT", "XPD", "XCU", "COPPER"}
CRYPTO_BASES = {
    "BTC", "ETH", "XRP", "SOL", "BNB", "LTC", "BCH", "ADA", "DOGE", "DOT", "LINK", "AVAX", "MATIC", "TRX", "XLM",
    "UNI", "ATOM", "ETC", "XMR", "EOS", "DASH", "ZEC", "NEAR", "FIL", "SHIB", "APT", "ARB", "OP", "SUI", "TON",
}
ENERGY_HINTS = ("BRENT", "WTI", "NGAS", "NATGAS", "USOIL", "UKOIL", "XTIUSD", "XBRUSD", "XNGUSD", "CRUDE", "HEATING")
INDEX_QUOTE_CCY = {
    "US30": "USD", "US500": "USD", "SPX500": "USD", "NAS100": "USD", "US100": "USD", "USTEC": "USD", "US2000": "USD",
    "DXY": "USD", "USDX": "USD", "VIX": "USD", "DE40": "EUR", "GER40": "EUR", "GER30": "EUR", "DAX": "EUR",
    "FR40": "EUR", "ES35": "EUR", "EU50": "EUR", "STOXX50": "EUR", "IT40": "EUR", "NL25": "EUR",
    "UK100": "GBP", "FTSE100": "GBP", "JP225": "JPY", "JPN225": "JPY", "HK50": "HKD", "HSI": "HKD",
    "AUS200": "AUD", "AU200": "AUD", "CN50": "USD", "CH20": "CHF",
}


@dataclass(frozen=True)
class AssetSpec:
    qty_step: float  # quantité minimale / pas (unités de base) = « 1 lot entier » pour les skills
    default_spread_bps: float  # spread estimé quand bid/ask sont absents (en points de base du prix)
    default_leverage_cap: float


SPECS = {
    "forex": AssetSpec(qty_step=1000.0, default_spread_bps=1.5, default_leverage_cap=30),
    "metal": AssetSpec(qty_step=0.1, default_spread_bps=3.0, default_leverage_cap=20),
    "energy": AssetSpec(qty_step=1.0, default_spread_bps=5.0, default_leverage_cap=10),
    "crypto": AssetSpec(qty_step=0.001, default_spread_bps=10.0, default_leverage_cap=5),
    "index": AssetSpec(qty_step=0.1, default_spread_bps=2.0, default_leverage_cap=20),
    "equity": AssetSpec(qty_step=1.0, default_spread_bps=5.0, default_leverage_cap=5),
    "other": AssetSpec(qty_step=1.0, default_spread_bps=10.0, default_leverage_cap=1),
}


def spec_for(asset_class: str) -> AssetSpec:
    return SPECS.get(asset_class, SPECS["other"])


def _split_pair(sym: str) -> tuple[str, str] | None:
    s = re.sub(r"[^A-Z]", "", sym.upper())
    if len(s) == 6 and s[:3] in CURRENCIES and s[3:] in CURRENCIES:
        return s[:3], s[3:]
    return None


def classify(symbol: str, raw_type: str = "", exchange: str = "", description: str = "") -> tuple[str, str | None, str | None]:
    """Retourne (classe d'actifs, devise de base, devise de cotation). Les métadonnées de la source priment."""
    sym = symbol.upper()
    t = (raw_type or "").lower()
    clean = re.sub(r"[^A-Z0-9]", "", sym)

    # 1. métadonnées explicites
    if any(k in t for k in ("crypto", "digital")):
        return "crypto", *_crypto_ccys(clean)
    if any(k in t for k in ("forex", "fx", "currenc")):
        pair = _split_pair(clean)
        return "forex", (pair[0] if pair else None), (pair[1] if pair else None)
    if any(k in t for k in ("metal", "gold", "silver")):
        return "metal", clean[:3], (clean[3:6] if len(clean) >= 6 and clean[3:6] in CURRENCIES else "USD")
    if any(k in t for k in ("energy", "oil", "gas")):
        return "energy", None, "USD"
    if any(k in t for k in ("index", "indices")):
        return "index", None, INDEX_QUOTE_CCY.get(clean, _index_ccy_guess(clean))
    if any(k in t for k in ("stock", "equit", "share")):
        return "equity", None, _equity_ccy(sym, exchange)

    # 2. heuristiques sur le symbole
    if clean[:3] in METALS or clean.startswith(("XAU", "XAG", "XPT", "XPD")):
        return "metal", clean[:3], (clean[3:6] if len(clean) >= 6 and clean[3:6] in CURRENCIES else "USD")
    if any(h in clean for h in ENERGY_HINTS):
        return "energy", None, "USD"
    base, quote = _crypto_ccys(clean)
    if base in CRYPTO_BASES and quote in CURRENCIES | {"USDT", "USDC"}:
        return "crypto", base, "USD" if quote in ("USDT", "USDC") else quote
    pair = _split_pair(clean)
    if pair:
        return "forex", pair[0], pair[1]
    if clean in INDEX_QUOTE_CCY:
        return "index", None, INDEX_QUOTE_CCY[clean]
    if re.fullmatch(r"[A-Z]{1,5}", clean) and exchange.upper() in ("NYSE", "NASDAQ", "US", "NYSE ARCA", "AMEX"):
        return "equity", None, "USD"
    if re.fullmatch(r"[A-Z]{1,5}", clean):
        return "equity", None, "USD"  # symbole court = action (hypothèse documentée)
    return "other", None, None


def _crypto_ccys(clean: str) -> tuple[str | None, str | None]:
    for n in (4, 3, 5):
        if clean[:n] in CRYPTO_BASES and clean[n:] in CURRENCIES | {"USDT", "USDC"}:
            return clean[:n], clean[n:]
    if len(clean) >= 6 and clean[-3:] in CURRENCIES:
        return clean[:-3], clean[-3:]
    return None, None


def _index_ccy_guess(clean: str) -> str | None:
    for k, v in INDEX_QUOTE_CCY.items():
        if clean.startswith(k):
            return v
    return None  # inconnu : le moteur refusera plutôt que de supposer


def _equity_ccy(symbol: str, exchange: str) -> str | None:
    s, ex = symbol.upper(), (exchange or "").upper()
    if s.endswith((".US", ".O", ".N")) or ex in ("NYSE", "NASDAQ", "US", "NYSE ARCA", "AMEX", ""):
        return "USD"
    if s.endswith((".L", ".UK")) or "LSE" in ex or ex == "UK":
        return "GBP"
    if s.endswith((".HK",)) or "HK" in ex:
        return "HKD"
    if s.endswith((".AX", ".AU")) or "ASX" in ex or ex == "AU":
        return "AUD"
    return None


def build_instrument(raw: dict) -> Instrument | None:
    """Construit un Instrument depuis une ligne du catalogue BiQuote (champs tolérants)."""
    low = {str(k).lower(): v for k, v in raw.items()}
    symbol = str(low.get("symbol") or low.get("name") or low.get("ticker") or "").strip().upper()
    if not symbol:
        return None
    raw_type = str(low.get("type") or low.get("category") or low.get("assetclass") or low.get("group") or "")
    exchange = str(low.get("exchange") or low.get("market") or "")
    desc = str(low.get("description") or low.get("desc") or low.get("fullname") or "")
    cls, base, quote = classify(symbol, raw_type, exchange, desc)

    def _num(*keys):
        for k in keys:
            v = low.get(k)
            try:
                if v is not None and float(v) > 0:
                    return float(v)
            except (TypeError, ValueError):
                continue
        return None

    digits = low.get("digits")
    try:
        digits = int(digits) if digits is not None else (3 if quote == "JPY" else 5 if cls == "forex" else 2)
    except (TypeError, ValueError):
        digits = 5
    return Instrument(symbol=symbol, description=desc, asset_class=cls, exchange=exchange, digits=digits,
                      tick_size=_num("ticksize", "tick_size", "point"), contract_size=_num("contractsize", "contract_size"),
                      base_ccy=base, quote_ccy=quote, live=bool(low.get("live") or low.get("islive") or False), raw_type=raw_type)


def session_profile(asset_class: str, exchange: str = "", symbol: str = "") -> str:
    """'us_equity' : session NYSE (le code des skills s'applique tel quel) ; sinon 'continuous'."""
    if asset_class == "equity":
        ex = (exchange or "").upper()
        s = symbol.upper()
        if ex in ("", "NYSE", "NASDAQ", "US", "NYSE ARCA", "AMEX") and not s.endswith((".L", ".HK", ".AX", ".UK", ".AU")):
            return "us_equity"
    return "continuous"
