"""WebDAV 异步客户端测试 — 路径编码 / GET / 递归 MKCOL / PUT（零网络 MockTransport）。"""
import sys, os, asyncio
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import httpx
import pytest
from services.webdav_client import WebDAVClient, WebDAVError


def test_build_url_percent_encodes_unicode_and_space():
    c = WebDAVClient(base_url="http://dav", username="", password="", root_path="/root")
    url = c.build_url("people/盐/盐见三省-tmdb-132776/folder.jpg")
    assert url == ("http://dav/root/people/%E7%9B%90/"
                   "%E7%9B%90%E8%A7%81%E4%B8%89%E7%9C%81-tmdb-132776/folder.jpg")
    assert c.build_url("movie/2023/Oppenheimer-tmdb-123456/poster.jpg") == \
        "http://dav/root/movie/2023/Oppenheimer-tmdb-123456/poster.jpg"


def test_build_url_sanitizes_double_slash():
    c = WebDAVClient(base_url="http://dav/", username="", password="", root_path="/dav/")
    assert c.build_url("movie/2023/x/poster.jpg").startswith("http://dav/dav/movie/2023/")


def test_aget_200_streams_and_404_returns_none():
    body = b"\xff\xd8\xff\xe0" + b"DATA"
    def handler(req):
        return httpx.Response(200, content=body, headers={"content-type": "image/jpeg"})
    c = WebDAVClient(base_url="http://dav", username="u", password="p",
                     transport=httpx.MockTransport(handler))

    async def main():
        resp = await c.aget("movie/2023/x/poster.jpg")
        assert resp is not None and resp.status_code == 200
        chunks = [ch async for ch in resp.aiter_bytes(chunk_size=1024)]
        await resp.aclose()
        assert b"".join(chunks) == body
        assert resp.headers["content-type"] == "image/jpeg"
    asyncio.run(main())

    c2 = WebDAVClient(base_url="http://dav", username="u", password="p",
                      transport=httpx.MockTransport(lambda req: httpx.Response(404)))
    async def miss():
        assert await c2.aget("movie/x/poster.jpg") is None
    asyncio.run(miss())


def test_aget_401_treated_as_miss():
    c = WebDAVClient(base_url="http://dav", username="u", password="p",
                     transport=httpx.MockTransport(lambda req: httpx.Response(401)))
    async def main():
        assert await c.aget("movie/x/poster.jpg") is None
    asyncio.run(main())


def test_ensure_collection_creates_missing_levels_in_order():
    calls = []
    def handler(req):
        calls.append((req.method, req.url.path))
        if req.url.path == "/movie/":
            return httpx.Response(405)          # 已存在
        return httpx.Response(201)              # 新建成功
    c = WebDAVClient(base_url="http://dav", username="u", password="p",
                     transport=httpx.MockTransport(handler))

    async def main():
        assert await c.ensure_collection("movie/2023/Oppenheimer-tmdb-123456/poster.jpg")
    asyncio.run(main())
    assert calls == [
        ("MKCOL", "/movie/"),
        ("MKCOL", "/movie/2023/"),
        ("MKCOL", "/movie/2023/Oppenheimer-tmdb-123456/"),
    ]


def test_ensure_collection_raises_on_403():
    c = WebDAVClient(base_url="http://dav", username="u", password="p",
                     transport=httpx.MockTransport(lambda req: httpx.Response(403)))
    with pytest.raises(WebDAVError):
        asyncio.run(c.ensure_collection("movie/2023/x/poster.jpg"))


def test_aput_streams_body_and_sets_content_type():
    received = {}
    async def handler(req):
        received["method"] = req.method
        received["body"] = await req.aread()
        received["ctype"] = req.headers.get("content-type")
        return httpx.Response(201)
    import io
    spool = io.BytesIO(b"\xff\xd8\xff\xe0" + b"X" * 5000)   # 模拟 spool
    c = WebDAVClient(base_url="http://dav", username="u", password="p",
                     transport=httpx.MockTransport(handler))

    async def main():
        assert await c.aput("movie/2023/x/poster.jpg", spool, "image/jpeg")
    asyncio.run(main())
    assert received["method"] == "PUT"
    assert received["body"].startswith(b"\xff\xd8\xff\xe0")
    assert len(received["body"]) == 5004
    assert received["ctype"] == "image/jpeg"
