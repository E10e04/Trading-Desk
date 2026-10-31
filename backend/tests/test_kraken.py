import asyncio
import json
import time

import httpx
import pytest
from websockets.asyncio.server import serve

from app.market.composite import CompositeProvider
from app.market.kraken import KrakenProvider
from app.market.provider import Instrument, Quote
from tests.test_market_stream import BiquoteProvider, _mock_rest

PAIRS = {
    "XXBTZUSD": {"altname": "XBTUSD", "wsname": "XBT/USD", "status": "online", "pair_decimals": 1, "tick_size": "0.1"},
    "XETHZUSD": {"altname": "ETHUSD", "wsname": "ETH/USD", "status": "online", "pair_decimals": 2},
    "XDGUSD": {"altname": "XDGUSD", "wsname": "XDG/USD", "status": "online", "pair_decimals": 6},
    "USDTUSD": {"altname": "USDTUSD", "wsname": "USDT/USD", "status": "online", "pair_decimals": 4},   # stablecoin : exclu
    "ZEURZUSD": {"altname": "EURUSD", "wsname": "EUR/USD", "status": "online", "pair_decimals": 5},    # devise : exclue
    "XXBTZEUR": {"altname": "XBTEUR", "wsname": "XBT/EUR", "status": "online", "pair_decimals": 1},    # pas en dollar : exclue
    "OLDUSD": {"altname": "OLDUSD", "wsname": "OLD/USD", "status": "cancel_only", "pair_decimals": 2},  # hors ligne : exclue
}
TICKER = {  # c = dernier prix, v/p = volume 24 h / vwap 24 h
    "XXBTZUSD": {"a": ["70001.0", "1", "1"], "b": ["70000.0", "1", "1"], "c": ["70000.5", "0.1"], "v": ["10", "1000"], "p": ["69000", "69500"]},
    "XETHZUSD": {"a": ["3500.1", "1", "1"], "b": ["3500.0", "1", "1"], "c": ["3500.0", "1"], "v": ["10", "100"], "p": ["3400", "3450"]},
    "XDGUSD": {"a": ["0.2001", "1", "1"], "b": ["0.2000", "1", "1"], "c": ["0.2", "1"], "v": ["10", "10"], "p": ["0.2", "0.2"]},
}


def rest_handler(calls: list):
    def h(req: httpx.Request):
        calls.append(req.url.path)
        if req.url.path == "/0/public/AssetPairs":
            return httpx.Response(200, json={"error": [], "result": PAIRS})
        if req.url.path == "/0/public/Ticker":
            return httpx.Response(200, json={"error": [], "result": TICKER})
        if req.url.path == "/0/public/OHLC":
            assert req.url.params["pair"] == "XBTUSD" and req.url.params["interval"] == "5"
            return httpx.Response(200, json={"error": [], "result": {"XXBTZUSD": [[1000, "1", "2", "0.5", "1.5", "1", "3", 4], [1300, "1.5", "3", "1", "2", "1", "3", 4], [1300, "1.5", "3", "1", "2", "1", "3", 4]], "last": 1300}})
        return httpx.Response(404)
    return h


async def mk(**kw):
    calls: list = []
    p = KrakenProvider(ws_url=kw.pop("ws_url", "ws://127.0.0.1:9"), http_transport=httpx.MockTransport(rest_handler(calls)), min_rest_gap=0.0,
                       reconnect_min=0.05, reconnect_max=0.2, ssl_context=None, **kw)
    p.calls = calls
    return p


async def test_catalogue_filtre_stablecoins_devises_et_classe_par_volume():
    p = await mk(top_n=2)
    insts = await p.list_instruments()
    assert [i.symbol for i in insts] == ["BTCUSD", "ETHUSD"]  # XBT -> BTC ; DOGE écarté par top_n ; USDT/EUR/hors ligne exclus
    assert insts[0].asset_class == "crypto" and insts[0].exchange == "Kraken" and insts[0].digits == 1
    assert p.pairs["BTCUSD"]["ws"] == "BTC/USD" and p.pairs["BTCUSD"]["alt"] == "XBTUSD"
    await p.aclose()


async def test_prix_rest_et_historique():
    p = await mk()
    await p.list_instruments()
    q = (await p.latest(["BTCUSD"]))["BTCUSD"]
    assert q.mid == 70000.5 and q.provider == "kraken" and q.market_state == "open" and q.bid == 70000.0
    bars = await p.history("BTCUSD", "5m", 100)
    assert [b.ts for b in bars] == [1000, 1300] and bars[-1].closed is False and bars[0].closed is True  # dédoublonné, trié
    with pytest.raises(Exception, match="inconnue"):
        await p.history("FOOUSD", "5m")
    await p.aclose()


async def test_flux_websocket_abonnement_groupe_et_ticks():
    seen: list[dict] = []

    async def handler(ws):
        async for raw in ws:
            m = json.loads(raw)
            seen.append(m)
            if m.get("method") == "subscribe":
                await ws.send(json.dumps({"channel": "ticker", "type": "snapshot", "data": [
                    {"symbol": "BTC/USD", "bid": 70000.0, "ask": 70002.0, "last": 70001.0},
                    {"symbol": "ETH/USD", "bid": 3500.0, "ask": 3500.2, "last": 3500.1}]}))
                await ws.send(json.dumps({"channel": "ticker", "type": "update", "data": [{"symbol": "BTC/USD", "bid": 70010.0, "ask": 70012.0, "last": 70011.0}]}))
                await ws.send(json.dumps({"channel": "ticker", "type": "update", "data": [{"symbol": "ZZZ/USD", "bid": 1.0, "ask": 1.1, "last": 1.0}]}))  # inconnu
                await ws.send(json.dumps({"channel": "heartbeat"}))

    async with serve(handler, "127.0.0.1", 0) as srv:
        port = srv.sockets[0].getsockname()[1]
        p = await mk(ws_url=f"ws://127.0.0.1:{port}")
        await p.list_instruments()
        got: list[Quote] = []
        await p.start_stream(lambda q: got.append(q))
        await p.subscribe(["BTCUSD", "ETHUSD"])
        for _ in range(50):
            if len(got) >= 3:
                break
            await asyncio.sleep(0.1)
        st = p.stream_status()
        await p.aclose()
    sub = [m for m in seen if m.get("method") == "subscribe"]
    assert len(sub) == 1 and sorted(sub[0]["params"]["symbol"]) == ["BTC/USD", "ETH/USD"]  # UN seul message pour tous les symboles
    assert sub[0]["params"]["channel"] == "ticker" and sub[0]["params"]["event_trigger"] == "bbo"
    assert [q.symbol for q in got[:3]] == ["BTCUSD", "ETHUSD", "BTCUSD"] and got[2].mid == 70011.0
    assert st["ticks_total"] >= 3 and st["ticks_invalid"] == 1 and st["state"] == "connected"


async def test_reconnexion_espacee_apres_coupure():
    connects: list[float] = []

    async def handler(ws):
        connects.append(time.monotonic())
        await ws.close()

    async with serve(handler, "127.0.0.1", 0) as srv:
        port = srv.sockets[0].getsockname()[1]
        p = await mk(ws_url=f"ws://127.0.0.1:{port}")
        await p.start_stream(lambda q: None)
        await asyncio.sleep(1.0)
        await p.aclose()
    assert 2 <= len(connects) <= 12  # pas de boucle serrée : plancher + temporisation croissante
    assert all(b - a >= 0.04 for a, b in zip(connects, connects[1:]))


async def test_rafraichissement_rest_si_flux_coupe():
    p = await mk(refresh_every=0.05)
    await p.list_instruments()
    got: list[Quote] = []
    await p.start_stream(lambda q: got.append(q))  # ws://127.0.0.1:9 : injoignable
    await p.subscribe(["BTCUSD"])
    for _ in range(60):
        if got:
            break
        await asyncio.sleep(0.1)
    st = p.stream_status()
    await p.aclose()
    assert got and got[0].symbol == "BTCUSD" and st["mode"] == "polling"


async def test_limiteur_rest_espace_les_appels():
    calls: list = []
    p = KrakenProvider(http_transport=httpx.MockTransport(rest_handler(calls)), min_rest_gap=0.2)
    t0 = time.monotonic()
    await asyncio.gather(p._get("/0/public/Ticker"), p._get("/0/public/Ticker"), p._get("/0/public/Ticker"))
    assert time.monotonic() - t0 >= 0.38  # 3 appels simultanés -> espacés d'au moins 0,2 s
    await p.aclose()


async def test_composite_route_les_cryptos_vers_kraken_et_le_reste_vers_biquote():
    k = await mk()
    b = BiquoteProvider(base_url="https://biquote.test", http_transport=_mock_rest())
    c = CompositeProvider(b, k)
    insts = {i.symbol: i for i in await c.list_instruments()}
    assert "EURUSD" in insts and insts["BTCUSD"].exchange == "Kraken" and insts["BTCUSD"].asset_class == "crypto"
    assert "AAPL" in insts
    lat = await c.latest(["EURUSD", "BTCUSD"])
    assert lat["BTCUSD"].provider == "kraken" and lat["EURUSD"].provider == "biquote"
    assert [b.ts for b in await c.history("BTCUSD", "5m", 10)] == [1000, 1300]
    assert (await c.history("EURUSD", "5m", 10))[0].o == 1  # BiQuote
    assert c.stream_status()["crypto"]["symbols"] == 3 and hasattr(c, "news")
    await c.aclose()


async def test_composite_garde_les_cryptos_de_biquote_si_kraken_est_injoignable():
    def down(req):
        raise httpx.ConnectError("hors ligne")
    k = KrakenProvider(http_transport=httpx.MockTransport(down), min_rest_gap=0.0)
    b = BiquoteProvider(base_url="https://biquote.test", http_transport=_mock_rest())
    c = CompositeProvider(b, k)
    insts = {i.symbol: i for i in await c.list_instruments()}
    assert insts["BTCUSD"].exchange != "Kraken" and c.crypto_error
    await c.aclose()
