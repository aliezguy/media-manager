"""WebDAV 图片代理服务测试 — 路径构造 / 缓存命中透传 / TMDB 兜底回写 / 失败场景（零网络）。"""
import sys, os, asyncio
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import httpx
import pytest
from fastapi import HTTPException
from services.webdav_client import WebDAVClient
import services.webdav_image_service as wis

JPEG = b"\xff\xd8\xff\xe0" + b"\x00\x00\x00" + b"A" * 2000
HTML = b"<html>anti-bot</html>"


# ---------- 路径构造 ----------
def test_build_media_webdav_rel_poster():
    rel = wis.build_media_webdav_rel("movie", 2023, "Oppenheimer", 123456, "poster")
    assert rel == "library/movie/2023/Oppenheimer-tmdb-123456/poster.jpg"


def test_build_media_webdav_rel_season_zero_pad():
    rel = wis.build_media_webdav_rel("tv", 2024, "Shogun", 98765, "season-poster", season=1)
    assert rel == "library/tv/2024/Shogun-tmdb-98765/season01-poster.jpg"


def test_parse_people_path():
    assert wis._parse_people_path("张/张译-tmdb-12345/folder.png") == \
        ("张/张译-tmdb-12345", "folder.png", "12345")
    assert wis._parse_people_path("bad-format.png") is None
    assert wis._parse_people_path("张/张译-12345/folder.png") is None   # 无 -tmdb-


def test_sanitize_dav_rel_rejects_traversal():
    with pytest.raises(HTTPException) as ei:
        wis._sanitize_dav_rel("../../etc/passwd")     # 路径穿越 → 400
    assert ei.value.status_code == 400
    assert wis._sanitize_dav_rel("张/张译-tmdb-1/folder.png") == "张/张译-tmdb-1/folder.png"


def test_build_actor_avatar_proxy_url():
    url = wis.build_actor_avatar_proxy_url("张/张译-tmdb-12345/folder.png")
    assert url == ("/api/webdav-image/people?path="
                   "%E5%BC%A0/%E5%BC%A0%E8%AF%91-tmdb-12345/folder.png")


def test_build_rel_sanitizes_name():
    assert wis.build_media_webdav_rel("movie", 2023, "a/b", 1, "poster") == "library/movie/2023/ab-tmdb-1/poster.jpg"
    assert wis.build_media_webdav_rel("movie", 2023, "", 1, "poster") == "library/movie/2023/unnamed-tmdb-1/poster.jpg"


# ---------- 缓存命中：WebDAV 200 → 透传，不碰 TMDB ----------
def _install_webdav(calls):
    def handler(req):
        calls.append((req.method, req.url.path))
        if req.method == "GET":
            return httpx.Response(200, content=JPEG, headers={"content-type": "image/jpeg"})
        return httpx.Response(201)
    c = WebDAVClient(base_url="http://dav", username="u", password="p",
                     transport=httpx.MockTransport(handler))
    return c


async def _collect(resp):
    return b"".join([chunk async for chunk in resp.body_iterator])


def test_cache_hit_serves_without_tmdb(monkeypatch):
    calls = []
    c = _install_webdav(calls)
    monkeypatch.setattr(wis, "get_webdav_client", lambda: c)

    async def tmdb_never():
        raise AssertionError("TMDB 不应被调用")
    monkeypatch.setattr(wis, "_fetch_tmdb_image_url", tmdb_never)

    async def main():
        resp = await wis.serve_media_image("movie", 123456, "Oppenheimer", 2023, "poster")
        body = await _collect(resp)
        assert body == JPEG
        assert resp.headers["content-type"] == "image/jpeg"
        assert resp.headers["cache-control"] == "public, max-age=31536000, immutable"
    asyncio.run(main())
    assert calls[0] == ("GET", "/library/movie/2023/Oppenheimer-tmdb-123456/poster.jpg")


# ---------- 缓存未命中：TMDB 兜底下载 → 前端 + 后台回写 ----------
def test_cache_miss_fetches_tmdb_and_writes_back(monkeypatch):
    dav_calls = []
    tmdb_calls = []
    img_calls = []

    def dav_handler(req):
        dav_calls.append((req.method, req.url.path))
        if req.method == "GET":
            return httpx.Response(404)
        return httpx.Response(201)

    def tmdb_meta_handler(req):
        tmdb_calls.append(req.url.path)
        assert "api_key" in req.url.params
        return httpx.Response(200, json={"poster_path": "/abc.jpg"})

    def tmdb_img_handler(req):
        img_calls.append(str(req.url))
        return httpx.Response(200, content=JPEG)

    monkeypatch.setattr(wis, "get_webdav_client",
                        lambda: WebDAVClient(base_url="http://dav", username="u", password="p",
                                             transport=httpx.MockTransport(dav_handler)))
    monkeypatch.setattr(wis, "_tmdb_meta_client",
                        httpx.AsyncClient(transport=httpx.MockTransport(tmdb_meta_handler)))
    monkeypatch.setattr(wis, "_tmdb_image_client",
                        httpx.AsyncClient(transport=httpx.MockTransport(tmdb_img_handler)))
    monkeypatch.setattr(wis, "_tmdb_credentials",
                        lambda: ("fakekey", "https://api.tmdb.org/3"))
    monkeypatch.setattr(wis, "budget_acquire", lambda *a, **k: True)

    async def main():
        resp = await wis.serve_media_image("movie", 123456, "Oppenheimer", 2023, "poster")
        body = await _collect(resp)
        assert body == JPEG
        await wis.wait_pending_writebacks()      # 等后台回写落地
    asyncio.run(main())

    assert tmdb_calls == ["/3/movie/123456"]   # base_url 含 /3，endpoint 直接拼接
    assert any(str(u).endswith("w500/abc.jpg") for u in img_calls)
    mkcols = [p for m, p in dav_calls if m == "MKCOL"]
    assert mkcols == ["/library/", "/library/movie/", "/library/movie/2023/",
                      "/library/movie/2023/Oppenheimer-tmdb-123456/"]
    puts = [p for m, p in dav_calls if m == "PUT"]
    assert puts == ["/library/movie/2023/Oppenheimer-tmdb-123456/poster.jpg"]


# ---------- 兜底失败场景 ----------
def test_tmdb_missing_poster_returns_404(monkeypatch):
    c = WebDAVClient(base_url="http://dav", username="u", password="p",
                     transport=httpx.MockTransport(lambda req: httpx.Response(404)))
    monkeypatch.setattr(wis, "get_webdav_client", lambda: c)
    monkeypatch.setattr(wis, "_tmdb_credentials", lambda: ("fakekey", "https://api.tmdb.org/3"))
    monkeypatch.setattr(wis, "budget_acquire", lambda *a, **k: True)
    monkeypatch.setattr(wis, "_tmdb_meta_client",
                        httpx.AsyncClient(transport=httpx.MockTransport(
                            lambda req: httpx.Response(200, json={"poster_path": None}))))

    async def main():
        with pytest.raises(HTTPException) as ei:
            await wis.serve_media_image("movie", 1, "X", 2023, "poster")
        assert ei.value.status_code == 404
    asyncio.run(main())


def test_tmdb_html_image_returns_404_no_put(monkeypatch):
    dav_calls = []
    def dav_handler(req):
        dav_calls.append((req.method, req.url.path))
        return httpx.Response(404) if req.method == "GET" else httpx.Response(201)
    monkeypatch.setattr(wis, "get_webdav_client",
                        lambda: WebDAVClient(base_url="http://dav", username="u", password="p",
                                             transport=httpx.MockTransport(dav_handler)))
    monkeypatch.setattr(wis, "_tmdb_credentials", lambda: ("fakekey", "https://api.tmdb.org/3"))
    monkeypatch.setattr(wis, "budget_acquire", lambda *a, **k: True)
    monkeypatch.setattr(wis, "_tmdb_meta_client",
                        httpx.AsyncClient(transport=httpx.MockTransport(
                            lambda req: httpx.Response(200, json={"poster_path": "/x.jpg"}))))
    monkeypatch.setattr(wis, "_tmdb_image_client",
                        httpx.AsyncClient(transport=httpx.MockTransport(
                            lambda req: httpx.Response(200, content=HTML))))

    async def main():
        with pytest.raises(HTTPException) as ei:
            await wis.serve_media_image("movie", 1, "X", 2023, "poster")
        assert ei.value.status_code == 404
    asyncio.run(main())
    assert not any(m == "PUT" for m, _ in dav_calls)     # 非图片绝不回写


def test_budget_exhausted_returns_404(monkeypatch):
    monkeypatch.setattr(wis, "get_webdav_client",
                        lambda: WebDAVClient(base_url="http://dav", username="u", password="p",
                                             transport=httpx.MockTransport(lambda req: httpx.Response(404))))
    monkeypatch.setattr(wis, "budget_acquire", lambda *a, **k: False)   # 预算满 → 跳过 TMDB

    async def main():
        with pytest.raises(HTTPException) as ei:
            await wis.serve_media_image("movie", 1, "X", 2023, "poster")
        assert ei.value.status_code == 404
    asyncio.run(main())


def test_unconfigured_returns_503(monkeypatch):
    monkeypatch.setattr(wis, "get_webdav_client", lambda: None)

    async def main():
        with pytest.raises(HTTPException) as ei:
            await wis.serve_media_image("movie", 1, "X", 2023, "poster")
        assert ei.value.status_code == 503
    asyncio.run(main())


# ==================== Task 4: Router（ASGITransport 隔离，不拉 database） ====================
import routers.webdav_image as wr
from fastapi import FastAPI
_test_app = FastAPI()
_test_app.include_router(wr.router, prefix="/api")   # 镜像 main.py 注册方式


def test_router_invalid_image_type_422():
    async def main():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=_test_app),
                                     base_url="http://t") as client:
            r = await client.get("/api/webdav-image/media",
                                 params={"media_type": "movie", "tmdb_id": 1, "name": "X",
                                         "year": 2023, "image_type": "banana"})
            assert r.status_code == 422
    asyncio.run(main())


def test_router_season_poster_without_season_400():
    async def main():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=_test_app),
                                     base_url="http://t") as client:
            r = await client.get("/api/webdav-image/media",
                                 params={"media_type": "tv", "tmdb_id": 1, "name": "X",
                                         "year": 2023, "image_type": "season-poster"})
            assert r.status_code == 400
    asyncio.run(main())


def test_router_returns_image_with_headers(monkeypatch):
    jpeg = b"\xff\xd8\xff\xe0" + b"B" * 1000
    def handler(req):
        return httpx.Response(200, content=jpeg, headers={"content-type": "image/jpeg"})
    monkeypatch.setattr(wis, "get_webdav_client",
                        lambda: WebDAVClient(base_url="http://dav", username="u", password="p",
                                             transport=httpx.MockTransport(handler)))

    async def main():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=_test_app),
                                     base_url="http://t") as client:
            r = await client.get("/api/webdav-image/people",
                                 params={"path": "张/张译-tmdb-12345/folder.png"})
            assert r.status_code == 200
            assert r.content == jpeg
            assert r.headers["cache-control"] == "public, max-age=31536000, immutable"
            assert r.headers["content-type"].startswith("image/")
    asyncio.run(main())
