"""WebDAV 图片代理服务 — 缓存优先 / TMDB 兜底 / 自动回写。

流程:
  1. 按媒体元数据拼接 WebDAV 相对路径（Emby/Jellyfin 本地媒体库规范）。
  2. WebDAV GET: 200 → 分块透传给前端（强缓存头）。
  3. 404 → 异步查 TMDB 元数据拿 poster/backdrop/profile_path → 分块下载到
     SpooledTemporaryFile（魔数校验）→ 前端分块流式 + 后台任务分块 PUT 回写。
  4. TMDB 也失败 → 404。

内存安全: 图片永不整体进内存；spool 超过 1MB 自动滚到磁盘临时文件，
close() 自动清理（POSIX 匿名临时文件，无需手动 unlink）。
回写放在响应 async 生成器的 finally（客户端断连也会执行；不能用
background=BackgroundTask —— Starlette 断连时跳过 background）。
"""
import asyncio
import logging
import os
import tempfile
import traceback
import urllib.parse

import httpx
from fastapi import HTTPException
from fastapi.responses import StreamingResponse

from config.settings import get_webdav_config, load_config
from services.actor_profile_service import is_image_content
from services.request_budget import acquire as budget_acquire
from services.webdav_client import WebDAVClient, WebDAVError

logger = logging.getLogger("uvicorn")

_CHUNK = 64 * 1024
_CACHE_HEADERS = {"Cache-Control": "public, max-age=31536000, immutable"}

# 魔数 → MIME（与 actor_profile_service 的 _IMAGE_MAGIC_CHECKS 同源，只需 MIME 映射）
_MAGIC_MIME = (
    (b"\xff\xd8\xff", "image/jpeg"),
    (b"\x89PNG\r\n\x1a\n", "image/png"),
    (b"RIFF", "image/webp"),
    (b"GIF8", "image/gif"),
)

# image_type → (TMDB 接口模板, 响应字段, 图片尺寸)
# 尺寸对齐既有约定: poster/season w500 (tmdb_service), backdrop w1280, profile w185 (actor_image_service)
_TMDB_SPEC = {
    "movie":  {"poster": ("/movie/{id}", "poster_path", "w500"),
               "backdrop": ("/movie/{id}", "backdrop_path", "w1280")},
    "tv":     {"poster": ("/tv/{id}", "poster_path", "w500"),
               "backdrop": ("/tv/{id}", "backdrop_path", "w1280")},
    "season": {"poster": ("/tv/{id}/season/{season}", "poster_path", "w500")},
    "people": {"folder": ("/person/{id}", "profile_path", "w185")},
}


# ---------- 路径构造 ----------
def _sanitize_name(name: str) -> str:
    name = "".join(ch for ch in (name or "").strip() if ch not in "/\\")
    return name or "unnamed"


def build_media_webdav_rel(media_type: str, year, name: str, tmdb_id,
                           image_type: str, season=None) -> str:
    name = _sanitize_name(name)
    dirname = f"{name}-tmdb-{tmdb_id}"
    if image_type == "season-poster":
        filename = f"season{int(season):02d}-poster.jpg"
    else:
        filename = f"{image_type}.jpg"
    return f"{media_type}/{year}/{dirname}/{filename}"


# ---------- 客户端单例（配置签名变化时重建，测试可 monkeypatch 注入） ----------
_webdav_client: WebDAVClient | None = None
_client_signature: tuple | None = None
_tmdb_meta_client: httpx.AsyncClient | None = None
_tmdb_image_client: httpx.AsyncClient | None = None
_tmdb_signature: tuple | None = None


def reset_clients() -> None:
    global _webdav_client, _client_signature, _tmdb_meta_client, _tmdb_image_client, _tmdb_signature
    _webdav_client = _tmdb_meta_client = _tmdb_image_client = None
    _client_signature = _tmdb_signature = None


def get_webdav_client() -> WebDAVClient | None:
    global _webdav_client, _client_signature
    cfg = get_webdav_config()
    sig = (cfg["base_url"], cfg["username"], cfg["password"], cfg["root_path"])
    if _webdav_client is None or sig != _client_signature:
        _client_signature = sig
        if not cfg["base_url"]:
            _webdav_client = None
            return None
        _webdav_client = WebDAVClient(**cfg)
    return _webdav_client


def _tmdb_credentials() -> tuple:
    cfg = load_config()
    api_key = os.environ.get("TMDB_API_KEY") or cfg.get("tmdb_api_key", "")
    base_url = cfg.get("tmdb_base_url", "") or "https://api.tmdb.org/3"
    return api_key, base_url


def _get_tmdb_meta_client() -> httpx.AsyncClient:
    global _tmdb_meta_client
    if _tmdb_meta_client is None:
        _tmdb_meta_client = httpx.AsyncClient(timeout=httpx.Timeout(5.0, read=15.0),
                                              follow_redirects=True)
    return _tmdb_meta_client


def _get_tmdb_image_client() -> httpx.AsyncClient:
    global _tmdb_image_client
    if _tmdb_image_client is None:
        _tmdb_image_client = httpx.AsyncClient(timeout=httpx.Timeout(10.0, read=60.0),
                                               follow_redirects=True)
    return _tmdb_image_client


# ---------- TMDB 兜底 ----------
async def _fetch_tmdb_image_url(media_key: str, tmdb_id, image_type: str, season=None) -> str:
    """按 image_type 请求对应 TMDB 接口，返回 image.tmdb.org 绝对 URL；失败返回 ''。"""
    endpoint, field, size = _TMDB_SPEC[media_key][image_type]
    api_key, base_url = _tmdb_credentials()
    if not api_key:
        return ""
    if not budget_acquire("tmdb", timeout=0):     # 预算满 → 立即跳过（不阻塞）
        logger.warning("   ⚠ [WebDAV] TMDB 预算满，跳过兜底")
        return ""
    url = f"{base_url}{endpoint.format(id=tmdb_id, season=season)}"
    try:
        resp = await _get_tmdb_meta_client().get(
            url, params={"api_key": api_key, "language": "zh-CN"})
        if resp.status_code != 200:
            return ""
        path = (resp.json().get(field) or "").strip()
    except httpx.HTTPError:
        logger.warning("   ⚠ [WebDAV] TMDB 元数据请求失败: %s", url)
        return ""
    if not path:
        return ""
    return f"https://image.tmdb.org/t/p/{size}{path}"


async def _download_tmdb_to_spool(spool, image_url: str) -> bytes | None:
    """分块流式下载 TMDB 图片到 spool，魔数校验；返回魔数（前 12 字节）或 None。"""
    client = _get_tmdb_image_client()
    req = client.build_request("GET", image_url)
    resp = await client.send(req, stream=True)
    try:
        if resp.status_code != 200:
            return None
        first = True
        magic = None
        async for chunk in resp.aiter_bytes(chunk_size=_CHUNK):
            if first:
                if not is_image_content(chunk[:4096]):     # CDN 反爬 HTML → 拒绝
                    return None
                magic = chunk[:12]
                first = False
            spool.write(chunk)
        return magic
    except httpx.HTTPError as e:
        logger.warning("   ⚠ [WebDAV] TMDB 图片下载异常: %s", e)
        return None
    finally:
        await resp.aclose()


def _magic_to_mime(magic: bytes) -> str:
    for sig, mime in _MAGIC_MIME:
        if magic.startswith(sig):
            return mime
    return "image/jpeg"


# ---- people 按 DB 地址驱动 ----
def _sanitize_dav_rel(path: str) -> str:
    """净化 people 相对地址：拒路径穿越(..)、当前段(.)与空段，直接 400。"""
    segs = [s for s in path.replace("\\", "/").strip("/").split("/") if s]
    if not segs or any(s in (".", "..") for s in segs):
        raise HTTPException(status_code=400, detail="无效头像地址")
    return "/".join(segs)


def _parse_people_path(local_image_path: str) -> tuple | None:
    """从 DB 地址解析 (dirname, filename, tmdb_id)。
    例: "张/张译-tmdb-12345/folder.png" → ("张/张译-tmdb-12345", "folder.png", "12345")
    """
    segs = local_image_path.replace("\\", "/").strip("/").split("/")
    if len(segs) != 3 or "-tmdb-" not in segs[1]:
        return None
    return f"{segs[0]}/{segs[1]}", segs[2], segs[1].split("-tmdb-", 1)[1]


def build_actor_avatar_proxy_url(local_image_path: str) -> str:
    """把 DB local_image_path 转成代理 URL（前端直接用）。"""
    return f"/api/webdav-image/people?path={urllib.parse.quote(_sanitize_dav_rel(local_image_path))}"


# ---------- 前端流式响应 ----------
def _webdav_hit_stream(resp) -> StreamingResponse:
    """WebDAV 命中：把 WebDAV 响应流分块透传，finally 里关闭底层连接。"""
    mime = resp.headers.get("content-type") or "image/jpeg"

    async def gen():
        try:
            async for chunk in resp.aiter_bytes(chunk_size=_CHUNK):
                yield chunk
        finally:
            await resp.aclose()

    return StreamingResponse(gen(), media_type=mime, headers=_CACHE_HEADERS)


async def _tmdb_miss_stream(webdav, dav_rel: str, media_key: str, tmdb_id,
                            image_type: str, season=None) -> StreamingResponse:
    """TMDB 兜底：下载→校验→spool 分块给前端→回写调度在 finally。"""
    image_url = await _fetch_tmdb_image_url(media_key, tmdb_id, image_type, season)
    if not image_url:
        raise HTTPException(status_code=404, detail="TMDB 无对应图片")

    spool = tempfile.SpooledTemporaryFile(max_size=1 * 1024 * 1024)
    magic = await _download_tmdb_to_spool(spool, image_url)
    if magic is None:
        spool.close()
        raise HTTPException(status_code=404, detail="TMDB 图片下载失败/非图片内容")
    mime = _magic_to_mime(magic)
    spool.seek(0)

    async def gen():
        try:
            while True:
                chunk = spool.read(_CHUNK)
                if not chunk:
                    break
                yield chunk
        finally:
            # 前端流结束（含客户端断连触发的 GeneratorExit）才回写，杜绝读指针竞争；
            # 此时 spool 已完整落盘，回写独占该文件。
            _schedule_write_back(webdav, dav_rel, spool, mime)

    return StreamingResponse(gen(), media_type=mime, headers=_CACHE_HEADERS)


# ---------- 后台回写（MKCOL 递归建目录 + 分块 PUT） ----------
_pending: set = set()


def _schedule_write_back(webdav, dav_rel: str, spool, mime: str) -> None:
    task = asyncio.create_task(_write_back(webdav, dav_rel, spool, mime))
    _pending.add(task)
    task.add_done_callback(_pending.discard)


async def _write_back(webdav, dav_rel: str, spool, mime: str) -> None:
    try:
        await webdav.ensure_collection(dav_rel)
        await webdav.aput(dav_rel, spool, mime)
    except WebDAVError as e:
        logger.warning("   ⚠ [WebDAV] 回写跳过: %s", e)
    except Exception:
        logger.warning("   ⚠ [WebDAV] 回写异常: %s", traceback.format_exc())
    finally:
        try:
            spool.close()
        except Exception:
            pass


async def wait_pending_writebacks() -> None:
    """测试/关停钩子：等所有后台回写落地。"""
    while _pending:
        await asyncio.gather(*list(_pending))


# ---------- 对外编排入口 ----------
async def serve_media_image(media_type: str, tmdb_id, name: str, year,
                            image_type: str, season=None) -> StreamingResponse:
    webdav = get_webdav_client()
    if webdav is None:
        raise HTTPException(status_code=503, detail="WebDAV 未配置")
    rel = build_media_webdav_rel(media_type, year, name, tmdb_id, image_type, season)
    resp = await webdav.aget(rel)
    if resp is not None:
        return _webdav_hit_stream(resp)
    return await _tmdb_miss_stream(webdav, rel, media_type, tmdb_id, image_type, season)


async def serve_people_image(local_image_path: str) -> StreamingResponse:
    """按 DB local_image_path 精确取头像。

    地址即 WebDAV people/ 下的相对路径（如 "张/张译-tmdb-12345/folder.png"）：
    命中→透传；未命中→从地址解析 tmdb_id 走 TMDB 兜底并回写同一地址。
    """
    webdav = get_webdav_client()
    if webdav is None:
        raise HTTPException(status_code=503, detail="WebDAV 未配置")
    rel = f"people/{_sanitize_dav_rel(local_image_path)}"
    resp = await webdav.aget(rel)
    if resp is not None:
        return _webdav_hit_stream(resp)
    parsed = _parse_people_path(local_image_path)
    if parsed is None:
        raise HTTPException(status_code=404, detail="头像地址格式无效")
    _dirname, _fname, tmdb_id = parsed
    return await _tmdb_miss_stream(webdav, rel, "people", tmdb_id, "folder")


# ---------- 本地 people 迁移到 WebDAV（一次性预填充 / 增量同步，幂等） ----------
# 定位项目根/people（与 main.py 的 PEOPLE_DIR 一致）
_PEOPLE_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "people")

_EXT_MIME = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
             ".webp": "image/webp", ".gif": "image/gif"}


async def migrate_local_people_to_webdav() -> dict:
    """把本地 people/ 增量同步到 WebDAV（幂等，PUT 覆盖）。

    遍历 people/{首字}/{name}-tmdb-{id}/folder.*，写到 WebDAV 同相对路径
    （保留原文件名与格式）。返回 {"uploaded", "failed"}。
    """
    webdav = get_webdav_client()
    if webdav is None:
        raise HTTPException(status_code=503, detail="WebDAV 未配置")
    if not os.path.isdir(_PEOPLE_DIR):
        return {"uploaded": 0, "failed": 0}
    stats = {"uploaded": 0, "failed": 0}
    for root, _dirs, files in os.walk(_PEOPLE_DIR):
        rel = os.path.relpath(root, _PEOPLE_DIR).replace(os.sep, "/")
        if rel == ".":
            continue
        for fname in files:
            if not fname.lower().startswith("folder"):
                continue
            dav_rel = f"people/{rel}/{fname}"      # ★ 保留原名/格式：folder.png 仍是 folder.png
            mime = _EXT_MIME.get(os.path.splitext(fname)[1].lower(), "image/jpeg")
            try:
                with open(os.path.join(root, fname), "rb") as f:
                    await webdav.ensure_collection(dav_rel)
                    ok = await webdav.aput(dav_rel, f, mime)
                stats["uploaded" if ok else "failed"] += 1
            except WebDAVError as e:
                stats["failed"] += 1
                logger.warning("   ⚠ [Migrate] %s: %s", os.path.join(root, fname), e)
    logger.info("   📦 [Migrate] people → WebDAV 完成: %s", stats)
    return stats
