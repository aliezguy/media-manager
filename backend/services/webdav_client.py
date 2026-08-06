"""WebDAV 异步客户端 — 图片代理的底层存取。

GET/PUT 均分块流式（不整图进内存）；PUT 前由 ensure_collection 递归 MKCOL
逐级创建缺失目录（409/405=已存在继续，404/409=父级缺失，401/403/5xx=致命）。
"""
import logging
import urllib.parse

import httpx

logger = logging.getLogger("uvicorn")


class WebDAVError(Exception):
    """WebDAV 操作失败（鉴权/权限/服务端错误），调用方应跳过回写。"""


class WebDAVClient:
    def __init__(self, base_url: str, username: str, password: str,
                 root_path: str = "", transport=None) -> None:
        self.base_url = base_url.rstrip("/")
        rp = (root_path or "").strip("/")
        self.root_path = f"/{rp}" if rp else ""
        self._client = httpx.AsyncClient(
            auth=(username, password) if username else None,
            transport=transport,
            follow_redirects=True,
            timeout=httpx.Timeout(5.0, read=30.0, write=60.0),
        )

    # ---- URL 构造：每段 quote，确定性编码，GET/MKCOL/PUT 共用 ----
    @staticmethod
    def _encode_path(rel: str) -> str:
        segs = [s for s in rel.strip("/").split("/") if s]
        return "/" + "/".join(urllib.parse.quote(s, safe="-_.~") for s in segs)

    def build_url(self, rel: str) -> str:
        return self.base_url + self.root_path + self._encode_path(rel)

    # ---- GET：200 返回未关闭的流式 Response（调用方 aiter_bytes + aclose）；
    #     404/401/403 关闭后返回 None（按缓存未命中兜底 TMDB）----
    async def aget(self, rel: str) -> httpx.Response | None:
        url = self.build_url(rel)
        try:
            resp = await self._client.get(url)
        except httpx.HTTPError as e:
            logger.warning("   ⚠ [WebDAV] GET 异常 %s: %s", url, e)
            return None
        if resp.status_code == 200:
            return resp
        await resp.aclose()
        if resp.status_code in (401, 403):
            logger.warning("   ⚠ [WebDAV] GET %s 鉴权失败 HTTP %d", url, resp.status_code)
        return None

    # ---- 递归 MKCOL：浅→深逐级建目录 ----
    async def ensure_collection(self, rel_file: str) -> bool:
        dirs = rel_file.strip("/").split("/")[:-1]
        acc = ""
        for s in dirs:
            acc = f"{acc}/{s}"
            resp = await self._client.request(
                "MKCOL", self.build_url(acc) + "/", follow_redirects=False)
            if resp.status_code in {200, 201, 204, 301, 302, 405}:
                continue                      # 已存在 / 成功
            if resp.status_code in {404, 409}:
                raise WebDAVError(f"MKCOL {acc}: 父级目录缺失（根不可创建）")
            raise WebDAVError(f"MKCOL {acc} 失败: HTTP {resp.status_code}")
        return True

    # ---- PUT：async 生成器分块上传（Transfer-Encoding: chunked），不整图进内存 ----
    async def aput(self, rel: str, spool, mime: str) -> bool:
        url = self.build_url(rel)
        spool.seek(0)
        resp = await self._client.put(
            url, content=_spool_chunks(spool), headers={"Content-Type": mime})
        if resp.status_code in (200, 201, 204):
            return True
        logger.warning("   ⚠ [WebDAV] PUT %s 失败 HTTP %d", url, resp.status_code)
        return False

    async def aclose(self) -> None:
        await self._client.aclose()


async def _spool_chunks(spool, size: int = 64 * 1024):
    """把 spool 文件对象按块 yield 给 httpx（异步迭代体 → chunked 上传）。"""
    spool.seek(0)
    while True:
        chunk = spool.read(size)
        if not chunk:
            break
        yield chunk
