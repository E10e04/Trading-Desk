"""Couche d'adaptation entre les données BiQuote (forex, métaux, crypto, indices, CFD actions) et les skills.

Les skills du dépôt source sont écrits pour des actions US : prix en USD arrondis au centime, quantités
en actions entières, session NYSE 9:30-16:00 (heure de New York), 78 bougies de 5 minutes par séance.

Deux adaptations, documentées dans docs/AGENTS.md, permettent de les exécuter SANS les modifier :

1. LOT VIRTUEL — `prix_virtuel = prix x taux_USD x taille_du_lot`. Chaque instrument ressemble ainsi à une
   « action » cotée en USD dont le centime est assez fin (pip de l'EURUSD, tick de l'or...). La quantité
   virtuelle (« actions ») = quantité réelle / taille_du_lot, donc dollars et pertes sont exacts. Les ratios
   (R, reward:risk, pourcentages) sont invariants. Le taux USD est figé pendant un calcul.

2. SESSION MAPPÉE — pour les marchés continus (forex, métaux, crypto, indices) une « séance » est une fenêtre
   de 6h30 démarrant à une heure d'ancrage UTC (par défaut 07:00 = ouverture de Londres). Les horodatages sont
   décalés pour que cette fenêtre tombe à 9:30-16:00 « heure de New York ». Les actions (profil us_equity)
   gardent leurs horodatages réels. Limite connue : le calendrier des skills (week-ends, jours fériés NYSE)
   s'applique aux séances mappées ; les cryptos ne sont donc pas tradées le week-end par les agents.
"""
from __future__ import annotations

import datetime as dt
import json
from dataclasses import dataclass
from typing import Any

from ..market.instruments import session_profile, spec_for
from ..market.provider import Bar, Instrument
from .loader import load_skill

ANCHOR_DEFAULT = "07:00"  # UTC
SESSION_MIN = 390  # 6h30
BAR = 300


# ----------------------------------------------------------------------------- lot virtuel
@dataclass(frozen=True)
class Virtual:
    symbol: str
    lot: float  # taille d'une « action virtuelle » en unités de base
    fx: float  # taux de la devise de cotation vers l'USD
    digits: int

    def vp(self, price: float) -> float:
        return price * self.fx * self.lot

    def rp(self, vprice: float) -> float:
        return round(vprice / (self.fx * self.lot), self.digits + 1)

    def vq(self, qty: float) -> float:
        return qty / self.lot

    def rq(self, vqty: float) -> float:
        return round(vqty * self.lot, 8)

    def usd_per_unit_move(self) -> float:
        return self.fx


MIN_VIRTUAL_PRICE = 300.0  # le centime d'une « action virtuelle » doit rester fin devant le prix (précision >= ~3e-6)


def make_virtual(inst: Instrument, fx: float | None, ref_price: float | None = None) -> Virtual:
    """Lot virtuel. Actions : lot = 1 (comportement exact du skill). Autres classes : lot = pas de quantité x 10^k,
    k le plus petit entier tel que le prix virtuel dépasse ~300 USD, pour que l'arrondi au centime reste négligeable
    (ex. BTC : 0,01 BTC ; or : 1 once ; EURUSD : 1 000 unités)."""
    if fx is None or fx <= 0:
        raise ValueError(f"conversion {inst.quote_ccy or 'devise inconnue'} → USD indisponible pour {inst.symbol}")
    step = spec_for(inst.asset_class).qty_step
    lot = step
    if inst.asset_class != "equity" and ref_price and ref_price > 0:
        vp = ref_price * fx * step
        k = 0
        while vp * 10 ** k < MIN_VIRTUAL_PRICE and k < 8:
            k += 1
        lot = step * 10 ** k
    return Virtual(inst.symbol, lot, float(fx), inst.digits or 5)


# ----------------------------------------------------------------------------- session mappée
def _eastern():
    return load_skill("entry")  # eastern_offset / to_eastern / eastern_to_utc (identiques dans chaque skill)


def parse_anchor(text: str) -> dt.time:
    h, m = text.split(":")
    return dt.time(int(h), int(m))


@dataclass(frozen=True)
class SessionMapper:
    profile: str  # us_equity | continuous
    anchor: dt.time = dt.time(7, 0)  # UTC, pour continuous

    @property
    def identity(self) -> bool:
        return self.profile == "us_equity"

    def _day_key(self, utc: dt.datetime) -> dt.datetime:
        a = dt.datetime.combine(utc.date(), self.anchor)
        return dt.datetime.combine(utc.date() if utc >= a else (utc - dt.timedelta(days=1)).date(), dt.time(0))

    def real_to_wall(self, utc: dt.datetime) -> dt.datetime:
        """Heure réelle (UTC naïf) -> heure « New York » mappée (naïve)."""
        if self.identity:
            return _eastern().to_eastern(utc)
        k = self._day_key(utc)
        anchor_dt = dt.datetime.combine(k.date(), self.anchor)
        return dt.datetime.combine(k.date(), dt.time(9, 30)) + (utc - anchor_dt)

    def wall_to_real(self, wall: dt.datetime) -> dt.datetime:
        if self.identity:
            return _eastern().eastern_to_utc(wall)
        anchor_dt = dt.datetime.combine(wall.date(), self.anchor)
        return anchor_dt + (wall - dt.datetime.combine(wall.date(), dt.time(9, 30)))

    def fake_utc(self, real_utc: dt.datetime) -> dt.datetime:
        """UTC « factice » tel que to_eastern(fake) == real_to_wall(real) : ce que les skills lisent."""
        if self.identity:
            return real_utc
        return _eastern().eastern_to_utc(self.real_to_wall(real_utc))

    def real_from_fake(self, fake_utc: dt.datetime) -> dt.datetime:
        if self.identity:
            return fake_utc
        return self.wall_to_real(_eastern().to_eastern(fake_utc))

    def fake_ts(self, ts: int) -> int:
        d = dt.datetime.fromtimestamp(ts, dt.timezone.utc).replace(tzinfo=None)
        return int(self.fake_utc(d).replace(tzinfo=dt.timezone.utc).timestamp())

    def real_ts_from_fake(self, ts: int) -> int:
        d = dt.datetime.fromtimestamp(ts, dt.timezone.utc).replace(tzinfo=None)
        return int(self.real_from_fake(d).replace(tzinfo=dt.timezone.utc).timestamp())


def mapper_for(inst: Instrument, anchor: str = ANCHOR_DEFAULT) -> SessionMapper:
    return SessionMapper(session_profile(inst.asset_class, inst.exchange, inst.symbol), parse_anchor(anchor))


# ----------------------------------------------------------------------------- conversion de barres
def to_virtual_bars(bars: list[Bar], v: Virtual, m: SessionMapper, only_closed: bool = True) -> list[dict]:
    """Bars réelles -> dicts {t,o,h,l,c,v} en prix virtuels et horodatage mappé (format `entry.parse_chart`)."""
    out = []
    for b in bars:
        if only_closed and not b.closed:
            continue
        out.append({"t": m.fake_ts(b.ts), "o": v.vp(b.o), "h": v.vp(b.h), "l": v.vp(b.l), "c": v.vp(b.c), "v": int(b.v or 0)})
    return out


def entry_chart(symbol: str, vbars: list[dict], data_time: int | None = None) -> dict:
    """Structure attendue par `entry.evaluate`."""
    prev = None
    return {"symbol": symbol, "meta": {"symbol": symbol, "dataGranularity": "5m"}, "bars": vbars, "prev_close": prev,
            "data_time": data_time}


def yahoo_doc(symbol: str, vbars: list[dict], regular_market_time: int | None = None) -> str:
    """Document JSON au format Yahoo v8 (entrée de `timemachine.time_machine` et du scanner)."""
    ts = [b["t"] for b in vbars]
    doc = {"chart": {"result": [{
        "meta": {"symbol": symbol, "dataGranularity": "5m", "range": "60d", **({"regularMarketTime": regular_market_time} if regular_market_time else {})},
        "timestamp": ts,
        "indicators": {"quote": [{k: [b[k] for b in vbars] for k in ("o", "h", "l", "c", "v")}]}}], "error": None}}
    q = doc["chart"]["result"][0]["indicators"]["quote"][0]
    q["open"], q["high"], q["low"], q["close"], q["volume"] = q.pop("o"), q.pop("h"), q.pop("l"), q.pop("c"), q.pop("v")
    return json.dumps(doc)


def scan_series(vbars: list[dict]) -> dict:
    """Série au format `scan.series_from_result` (listes t, c, o, h, l, v)."""
    return {"t": [b["t"] for b in vbars], "c": [b["c"] for b in vbars], "o": [b["o"] for b in vbars],
            "h": [b["h"] for b in vbars], "l": [b["l"] for b in vbars], "v": [b["v"] for b in vbars], "meta": {}}


def bars_from_yahoo_text(text: str) -> list[Bar]:
    """Lit un JSON Yahoo (fixtures du dépôt source) en Bars réelles — pour les tests de fidélité."""
    res = json.loads(text)["chart"]["result"][0]
    q = res["indicators"]["quote"][0]
    out = []
    for i, t in enumerate(res.get("timestamp") or []):
        c = q["close"][i]
        if c is None:
            continue
        o = q["open"][i] if q["open"][i] is not None else c
        h = q["high"][i] if q["high"][i] is not None else c
        lo = q["low"][i] if q["low"][i] is not None else c
        out.append(Bar(ts=int(t), o=o, h=h, l=lo, c=c, v=float((q.get("volume") or [0] * (i + 1))[i] or 0)))
    return out


def now_fake(m: SessionMapper, now_utc: dt.datetime) -> dt.datetime:
    return m.fake_utc(now_utc)


# ----------------------------------------------------------------------------- codes de tickers pour le skill Risk
def risk_code(symbol: str, table: dict[str, str]) -> str:
    """Le skill Risk n'accepte que des tickers d'actions (1 à 5 lettres). On lui passe un code d'actions
    stable et réversible ; la classe d'actifs est contrôlée par les règles déterministes du backend."""
    if symbol not in table:
        n, code = len(table), ""
        for _ in range(4):
            code = chr(65 + n % 26) + code
            n //= 26
        table[symbol] = "Z" + code
    return table[symbol]
