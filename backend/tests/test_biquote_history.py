import datetime as dt

from app.market.biquote import BiquoteProvider

BASE = int(dt.datetime(2026, 10, 1, tzinfo=dt.timezone.utc).timestamp())


def _row(i):
    return {"t": dt.datetime.fromtimestamp(BASE + i * 300, dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "o": 1.0, "h": 1.1, "l": 0.9, "c": 1.0, "v": 1}


async def test_history_remonte_le_temps_par_pages_de_1000():
    total = 2500
    calls = []
    p = BiquoteProvider(base_url="http://x", api_key="")

    async def fake_get(path, params=None):
        calls.append(dict(params))
        end = total
        if "to" in params:
            t = dt.datetime.strptime(params["to"], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=dt.timezone.utc).timestamp()
            end = int((t - BASE) // 300) + 1
        lo = max(0, end - params["limit"])
        return {"bars": [_row(i) for i in range(lo, end)]}

    p._get = fake_get  # type: ignore[method-assign]
    bars = await p.history("EURUSD", "5m", limit=2300)
    assert len(bars) == 2300 and len(calls) == 3
    ts = [b.ts for b in bars]
    assert ts == sorted(set(ts)) and ts[-1] == BASE + (total - 1) * 300
    small = await p.history("EURUSD", "5m", limit=500)
    assert len(small) == 500 and len(calls) == 4
