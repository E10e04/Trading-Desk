import httpx
import pytest

from app.bus import EventBus
from app.market import barsdb
from app.market.feed import MarketFeed
from app.market.provider import Bar, Instrument
from app.market.yahoo import YahooHistory, parse_chart, yahoo_symbol

NOW = 1_800_000_000


def payload(n=5, start=NOW - 5 * 300 - 600):
    ts = [start + i * 300 for i in range(n)]
    px = [1.1 + i * 0.001 for i in range(n)]
    return {"chart": {"result": [{"timestamp": ts, "indicators": {"quote": [{"open": px, "high": [p + 0.001 for p in px], "low": [p - 0.001 for p in px],
                                                                              "close": px, "volume": [0] * n}]}}], "error": None}}


def test_parse_ignore_les_trous_et_la_bougie_en_cours():
    p = payload()
    p["chart"]["result"][0]["indicators"]["quote"][0]["close"][1] = None      # trou chez Yahoo
    bars = parse_chart(p, now=NOW)
    assert len(bars) == 4
    assert all(isinstance(b, Bar) for b in bars)
    last = parse_chart(payload(start=NOW - 100), now=NOW)[-1]
    assert last.closed is False                                              # bougie encore ouverte


def test_symboles_connus_seulement():
    fx = Instrument(symbol="EURUSD", asset_class="forex", base_ccy="EUR", quote_ccy="USD")
    assert yahoo_symbol(fx) == "EURUSD=X"
    assert yahoo_symbol(Instrument(symbol="XAUUSD", asset_class="metal")) == "XAUUSD=X"
    assert yahoo_symbol(Instrument(symbol="BTCUSD", asset_class="crypto", base_ccy="BTC", quote_ccy="USD")) == "BTC-USD"
    assert yahoo_symbol(Instrument(symbol="US500", asset_class="index")) == "^GSPC"
    assert yahoo_symbol(Instrument(symbol="AAPL", asset_class="equity")) == "AAPL"
    assert yahoo_symbol(Instrument(symbol="OBSCURE1", asset_class="other")) is None   # on ne devine pas


async def test_client_http_erreurs_claires():
    ok = YahooHistory(transport=httpx.MockTransport(lambda r: httpx.Response(200, json=payload())))
    assert len(await ok.bars_5m("EURUSD=X")) >= 4
    bad = YahooHistory(transport=httpx.MockTransport(lambda r: httpx.Response(429)))
    with pytest.raises(Exception, match="429"):
        await bad.bars_5m("EURUSD=X")


class Prov:
    name = "t"

    async def history(self, symbol, tf, limit=500, **kw):
        base = 1_799_990_000 // 300 * 300
        return [Bar(ts=base + i * 300, o=2, h=2, l=2, c=2, v=1, closed=True) for i in range(100)]


class FakeYahoo:
    def __init__(self):
        self.calls = 0

    async def bars_5m(self, ysym, days=60):
        self.calls += 1
        base = 1_799_990_000 // 300 * 300 - 3 * 86400
        return [Bar(ts=base + i * 300, o=1, h=1, l=1, c=1, v=0, closed=True) for i in range(500)]


async def test_historique_profond_comblé_par_yahoo_sans_ecraser_biquote(db):
    f = MarketFeed(Prov(), EventBus(), live_s=30, hard_stale_s=300)
    f.instruments["EURUSD"] = Instrument(symbol="EURUSD", asset_class="forex", base_ccy="EUR", quote_ccy="USD")
    f.persist_bars = True
    f.yahoo = FakeYahoo()
    f.backfill_enabled = lambda: True
    deep = await f._history("EURUSD", "5m", 5000)
    assert len(deep) > 100                                   # BiQuote (100) + Yahoo plus ancien
    assert f.yahoo.calls == 1
    assert [b.ts for b in deep] == sorted({b.ts for b in deep})
    assert deep[-1].c == 2                                   # les bougies BiQuote gardent leurs valeurs
    f._hist_cache.clear()
    await f._history("EURUSD", "5m", 5000)
    assert f.yahoo.calls == 1                                # pas de nouvel appel avant 6 h
    f2 = MarketFeed(Prov(), EventBus(), live_s=30, hard_stale_s=300)
    f2.instruments["EURUSD"] = f.instruments["EURUSD"]
    f2.persist_bars, f2.yahoo = True, FakeYahoo()            # réglage désactivé : aucun appel
    await f2._history("EURUSD", "5m", 5000)
    assert f2.yahoo.calls == 0
    shallow = MarketFeed(Prov(), EventBus(), live_s=30, hard_stale_s=300)
    shallow.instruments["EURUSD"] = f.instruments["EURUSD"]
    shallow.persist_bars, shallow.yahoo, shallow.backfill_enabled = True, FakeYahoo(), (lambda: True)
    await shallow._history("EURUSD", "5m", 300)             # graphique normal : Yahoo n'est pas sollicité
    assert shallow.yahoo.calls == 0
