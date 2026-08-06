"""WebDAV 头像主动推送 — 演员头像下载更新后 fire-and-forget 回推。

作用：`serve_people_image` 代理是 cache-first 读取，若头像更新后不主动回推，
WebDAV 里存的会是旧图，代理永远读到旧头像。本模块在每次真正下载了新头像后，
把本地文件 PUT 到 WebDAV 同路径，保证缓存新鲜。

设计约束：
- 不能 import webdav_image_service（它有循环依赖：webdav_image_service 引用了
  actor_profile_service 的 is_image_content），故只依赖 webdav_client + config.settings。
- 头像下载流程（resolve_actor_profile）是同步函数，WebDAV client 是异步的：
  用模块级 ThreadPoolExecutor 提交，worker 线程内 asyncio.run 跑一次性 loop
  （每个 worker 新建并关闭自己的 WebDAVClient，避免 async client 跨 loop 复用）。
- max_workers=4 限制批量汉化时的并发 PUT；推送失败只告警，绝不影响汉化流程。
"""
import asyncio
import concurrent.futures
import logging
import os
import threading

from config.settings import get_webdav_config
from services.webdav_client import WebDAVClient, WebDAVError

logger = logging.getLogger("uvicorn")

# 本地 people 根目录（与 main.py 的 PEOPLE_DIR / webdav_image_service._PEOPLE_DIR 一致）
_PEOPLE_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "people")

_EXT_MIME = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
             ".webp": "image/webp", ".gif": "image/gif"}

# 模块级线程池：批量汉化 50 个演员时最多 4 个并发 PUT，不爆线程
_executor = concurrent.futures.ThreadPoolExecutor(max_workers=4,
                                                  thread_name_prefix="webdav-push")
_pending: set = set()
_lock = threading.Lock()


def _sanitize_rel(local_image_path: str) -> str | None:
    """净化 people 相对地址：拒路径穿越，返回段间用 / 的干净路径。"""
    segs = [s for s in local_image_path.replace("\\", "/").strip("/").split("/") if s]
    if not segs or any(s in (".", "..") for s in segs):
        return None
    return "/".join(segs)


def _mime_from_ext(path: str) -> str:
    return _EXT_MIME.get(os.path.splitext(path)[1].lower(), "image/jpeg")


async def _push_async(cfg: dict, abs_path: str, rel: str, mime: str) -> None:
    """在 worker 线程的独立事件循环里执行单次 PUT（client 生命周期绑定该 loop）。"""
    client = WebDAVClient(
        cfg["base_url"], cfg.get("username", ""), cfg.get("password", ""),
        cfg.get("root_path", ""))
    try:
        await client.ensure_collection(rel)
        with open(abs_path, "rb") as f:
            await client.aput(rel, f, mime)
    finally:
        await client.aclose()


def _run_push(cfg: dict, abs_path: str, rel: str, mime: str) -> None:
    try:
        asyncio.run(_push_async(cfg, abs_path, rel, mime))
        logger.info("   📤 [WebDAV-Push] 头像已回推: %s", rel)
    except Exception as e:  # noqa: BLE001 — 推送失败绝不抛出（后台任务）
        logger.warning("   ⚠ [WebDAV-Push] 回推失败 %s: %s", rel, e)


def push_actor_avatar_to_webdav(local_image_path: str) -> None:
    """本地头像更新后主动回推 WebDAV（fire-and-forget）。WebDAV 未配置/文件缺失时静默跳过。"""
    cfg = get_webdav_config()
    if not cfg.get("base_url"):
        return
    rel = _sanitize_rel(local_image_path)
    if rel is None:
        return
    abs_path = os.path.join(_PEOPLE_DIR, *rel.split("/"))
    if not os.path.isfile(abs_path):
        return
    mime = _mime_from_ext(abs_path)
    fut = _executor.submit(_run_push, cfg, abs_path, f"{_people_root(cfg)}/people/{rel}", mime)
    with _lock:
        _pending.add(fut)
    fut.add_done_callback(lambda f: _pending.discard(f))


def _people_root(cfg: dict) -> str:
    """people 的上级目录（config: webdav_people_root，默认 library）。"""
    return (cfg.get("people_root") or "library").strip("/") or "library"


def flush_pushes() -> None:
    """测试/关停钩子：等待所有后台推送落地。"""
    for fut in list(_pending):
        fut.result()
