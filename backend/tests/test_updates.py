import httpx
import pytest

from app import updates

REL = [
    {"tag_name": "v0.1.0", "name": "0.1.0", "draft": False, "prerelease": False, "published_at": "2026-10-30T00:00:00Z", "html_url": "u1", "body": "a", "assets": []},
    {"tag_name": "v0.10.0", "name": "0.10.0", "draft": False, "prerelease": False, "published_at": "2026-12-01T00:00:00Z", "html_url": "u10", "body": "b",
     "assets": [{"name": "Trading Desk Setup 0.10.0.exe", "browser_download_url": "https://github.com/x/y/releases/download/v0.10.0/s.exe", "size": 9, "digest": "sha256:abc"},
                {"name": "Trading Desk-0.10.0.dmg", "browser_download_url": "https://github.com/x/y/releases/download/v0.10.0/d.dmg", "size": 8},
                {"name": "latest.yml", "browser_download_url": "z"}]},
    {"tag_name": "v0.9.0", "draft": False, "prerelease": False, "assets": []},
    {"tag_name": "v1.0.0-beta", "draft": False, "prerelease": True, "assets": []},
    {"tag_name": "v2.0.0", "draft": True, "prerelease": False, "assets": []},
    {"tag_name": "nightly", "draft": False, "prerelease": False, "assets": []},
]


def test_version_semantique_pas_alphabetique():
    out = updates.summarize(REL, "0.1.0")
    assert out["latest"]["version"] == "0.10.0"  # 0.10 > 0.9 ; brouillons, préversions et tags non semver ignorés
    assert [r["version"] for r in out["releases"]] == ["0.10.0", "0.9.0", "0.1.0"]
    assert out["update_available"] is True
    assert {a["platform"] for a in out["latest"]["assets"]} == {"win", "mac"}  # latest.yml ignoré
    assert out["latest"]["assets"][0]["sha256"] == "abc"


def test_a_jour_quand_la_version_est_identique():
    assert updates.summarize(REL, "0.10.0")["update_available"] is False
    assert updates.parse_version("v1.2.3") == (1, 2, 3) and updates.parse_version("abc") is None


async def test_check_http_et_erreurs():
    calls = []

    def ok(req):
        calls.append(req.url.path)
        return httpx.Response(200, json=REL)
    updates._cache["data"] = None
    async with httpx.AsyncClient(transport=httpx.MockTransport(ok)) as c:
        r = await updates.check("o/r", "0.1.0", force=True, client=c)
        assert r["update_available"] and calls == ["/repos/o/r/releases"]
        r2 = await updates.check("o/r", "0.10.0", client=c)  # en cache, recalculé pour la version courante
        assert not r2["update_available"] and len(calls) == 1
    for status, msg in ((404, "introuvables"), (403, "limite")):
        async with httpx.AsyncClient(transport=httpx.MockTransport(lambda req, s=status: httpx.Response(s))) as c:
            with pytest.raises(RuntimeError, match=msg):
                await updates.check("o/r2", "0.1.0", force=True, client=c)
