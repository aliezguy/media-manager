"""people 头像迁移 + 按 DB 地址驱动读取测试（零网络 MockTransport）。"""
import sys, os, asyncio
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import httpx
import pytest
from fastapi import HTTPException
from services.webdav_client import WebDAVClient
import services.webdav_image_service as wis

JPEG = b"\xff\xd8\xff\xe0" + b"X" * 500


def test_migrate_uploads_and_creates_dirs(monkeypatch, tmp_path):
    # 造本地 people 结构: people/张/张译-tmdb-12345/folder.png
    person_dir = tmp_path / "张" / "张译-tmdb-12345"
    person_dir.mkdir(parents=True)
    (person_dir / "folder.png").write_bytes(JPEG)
    monkeypatch.setattr(wis, "_PEOPLE_DIR", str(tmp_path))

    calls = []
    def handler(req):
        calls.append((req.method, req.url.raw_path.decode()))   # raw_path=百分号编码（服务器实际收到）
        return httpx.Response(201)
    monkeypatch.setattr(wis, "get_webdav_client",
                        lambda: WebDAVClient(base_url="http://dav", username="u", password="p",
                                             transport=httpx.MockTransport(handler)))

    async def main():
        stats = await wis.migrate_local_people_to_webdav()
        assert stats == {"uploaded": 1, "failed": 0}
    asyncio.run(main())

    mkcols = [p for m, p in calls if m == "MKCOL"]
    assert mkcols == ["/library/", "/library/people/", "/library/people/%E5%BC%A0/",
                      "/library/people/%E5%BC%A0/%E5%BC%A0%E8%AF%91-tmdb-12345/"]
    puts = [p for m, p in calls if m == "PUT"]
    assert puts == ["/library/people/%E5%BC%A0/%E5%BC%A0%E8%AF%91-tmdb-12345/folder.png"]  # 保留原名/格式


def test_migrate_no_people_dir(monkeypatch):
    monkeypatch.setattr(wis, "_PEOPLE_DIR", "/nonexistent/people")
    monkeypatch.setattr(wis, "get_webdav_client",
                        lambda: WebDAVClient(base_url="http://dav", username="u", password="p",
                                             transport=httpx.MockTransport(lambda r: httpx.Response(201))))
    async def main():
        assert await wis.migrate_local_people_to_webdav() == {"uploaded": 0, "failed": 0}
    asyncio.run(main())


def test_migrate_unconfigured_503(monkeypatch):
    monkeypatch.setattr(wis, "get_webdav_client", lambda: None)
    async def main():
        with pytest.raises(HTTPException) as ei:
            await wis.migrate_local_people_to_webdav()
        assert ei.value.status_code == 503
    asyncio.run(main())


def test_serve_people_by_db_path(monkeypatch):
    # 按 DB 地址精确取图：GET people/张/张译-tmdb-12345/folder.png → 200 透传，零探测
    calls = []
    def handler(req):
        calls.append(req.url.raw_path.decode())   # raw_path=百分号编码（服务器实际收到）
        return httpx.Response(200, content=JPEG, headers={"content-type": "image/jpeg"})
    monkeypatch.setattr(wis, "get_webdav_client",
                        lambda: WebDAVClient(base_url="http://dav", username="u", password="p",
                                             transport=httpx.MockTransport(handler)))

    async def main():
        resp = await wis.serve_people_image("张/张译-tmdb-12345/folder.png")
        body = b"".join([ch async for ch in resp.body_iterator])
        assert body == JPEG
    asyncio.run(main())
    assert calls == ["/library/people/%E5%BC%A0/%E5%BC%A0%E8%AF%91-tmdb-12345/folder.png"]


def test_serve_people_respects_configured_people_root(monkeypatch):
    calls = []
    def handler(req):
        calls.append(req.url.raw_path.decode())
        return httpx.Response(200, content=JPEG, headers={"content-type": "image/jpeg"})
    monkeypatch.setattr(wis, "get_webdav_client",
                        lambda: WebDAVClient(base_url="http://dav", username="u", password="p",
                                             transport=httpx.MockTransport(handler)))
    monkeypatch.setattr(wis, "get_webdav_config",
                        lambda: {"base_url": "", "username": "", "password": "", "root_path": "",
                                 "media_root": "library", "people_root": "actors"})

    async def main():
        resp = await wis.serve_people_image("张/张译-tmdb-12345/folder.png")
        b"".join([ch async for ch in resp.body_iterator])
    asyncio.run(main())
    assert calls == ["/actors/people/%E5%BC%A0/%E5%BC%A0%E8%AF%91-tmdb-12345/folder.png"]


def test_people_miss_writes_back_to_same_path(monkeypatch):
    # DB 地址在 WebDAV 未命中 → TMDB 兜底 → 回写同一地址 folder.png（地址=唯一真相）
    PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 300
    dav_calls = []
    def dav_handler(req):
        dav_calls.append((req.method, req.url.raw_path.decode()))
        return httpx.Response(404) if req.method == "GET" else httpx.Response(201)
    monkeypatch.setattr(wis, "get_webdav_client",
                        lambda: WebDAVClient(base_url="http://dav", username="u", password="p",
                                             transport=httpx.MockTransport(dav_handler)))
    monkeypatch.setattr(wis, "_tmdb_credentials", lambda: ("fakekey", "https://api.tmdb.org/3"))
    monkeypatch.setattr(wis, "budget_acquire", lambda *a, **k: True)
    monkeypatch.setattr(wis, "_tmdb_meta_client",
                        httpx.AsyncClient(transport=httpx.MockTransport(
                            lambda req: httpx.Response(200, json={"profile_path": "/p.png"}))))
    monkeypatch.setattr(wis, "_tmdb_image_client",
                        httpx.AsyncClient(transport=httpx.MockTransport(
                            lambda req: httpx.Response(200, content=PNG))))

    async def main():
        resp = await wis.serve_people_image("张/张译-tmdb-12345/folder.png")
        body = b"".join([ch async for ch in resp.body_iterator])
        assert body == PNG
        await wis.wait_pending_writebacks()
    asyncio.run(main())
    gets = [p for m, p in dav_calls if m == "GET"]
    assert gets == ["/library/people/%E5%BC%A0/%E5%BC%A0%E8%AF%91-tmdb-12345/folder.png"]
    puts = [p for m, p in dav_calls if m == "PUT"]
    assert puts == ["/library/people/%E5%BC%A0/%E5%BC%A0%E8%AF%91-tmdb-12345/folder.png"]
