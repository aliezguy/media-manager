"""
CloudDrive2 gRPC Service

Provides gRPC client to communicate with CloudDrive2 server for:
- Authentication (token-based)
- Directory/file listing
- File information lookup

CD2 Server: 192.168.31.173:19797
"""

import os
import time
import logging
from datetime import datetime, timezone
from functools import lru_cache
from typing import Optional

import grpc

from services.clouddrive_pb import clouddrive_pb2, clouddrive_pb2_grpc

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Configuration resolver — config.json first, then env vars (env wins)
# ---------------------------------------------------------------------------
@lru_cache()
def _cd2_config() -> dict:
    """Load CD2 config from config.json, falling back to env vars."""
    try:
        from config.settings import load_config
        cfg = load_config()
    except Exception:
        cfg = {}

    def _val(key: str, env_key: str, default: str = "") -> str:
        # env var takes highest priority, then config.json, then default
        env_val = os.getenv(env_key)
        if env_val is not None and env_val != "":
            return env_val
        return cfg.get(key) or default

    return {
        "host": _val("cd2_host", "CD2_HOST", "192.168.31.173"),
        "port": _val("cd2_port", "CD2_PORT", "19797"),
        "username": _val("cd2_username", "CD2_USERNAME", ""),
        "password": _val("cd2_password", "CD2_PASSWORD", ""),
        "media_dir": _val(
            "cd2_media_dir", "CD2_MEDIA_DIR",
            "/80003588/emby库/电视剧/国产剧/",
        ),
        "organized_dir": _val(
            "cd2_organized_dir", "CD2_ORGANIZED_DIR",
            "/80003588/网盘整理/完结整理/电视剧/国产剧",
        ),
    }


# Module-level config accessors (plain functions — property descriptor
# doesn't work at module level)
def _get_cd2_host() -> str:
    return _cd2_config()["host"]

def _get_cd2_port() -> str:
    return _cd2_config()["port"]

def _get_cd2_username() -> str:
    return _cd2_config()["username"]

def _get_cd2_password() -> str:
    return _cd2_config()["password"]

def get_cd2_media_dir() -> str:
    return _cd2_config()["media_dir"]

def get_cd2_organized_dir() -> str:
    return _cd2_config()["organized_dir"]

# ---------------------------------------------------------------------------
# Helper: convert CloudDriveFile protobuf → plain dict
# ---------------------------------------------------------------------------
def _file_to_dict(f: clouddrive_pb2.CloudDriveFile) -> dict:
    """Convert a single CloudDriveFile protobuf message to a plain dict."""
    file_type_map = {
        clouddrive_pb2.CloudDriveFile.Directory: "directory",
        clouddrive_pb2.CloudDriveFile.File: "file",
        clouddrive_pb2.CloudDriveFile.Other: "other",
    }

    # Convert google.protobuf.Timestamp → ISO-8601 string (or None)
    def _ts(proto_ts):
        if proto_ts and proto_ts.seconds:
            return datetime.fromtimestamp(
                proto_ts.seconds + proto_ts.nanos * 1e-9, tz=timezone.utc
            ).isoformat()
        return None

    result = {
        "id": f.id,
        "name": f.name,
        "fullPathName": f.fullPathName,
        "size": f.size,
        "fileType": file_type_map.get(f.fileType, "unknown"),
        "isDirectory": f.isDirectory,
        "createTime": _ts(f.createTime),
        "writeTime": _ts(f.writeTime),
        "accessTime": _ts(f.accessTime),
        "isForbidden": f.isForbidden,
        "isLocal": f.isLocal,
        "readOnly": f.readOnly,
        "thumbnailUrl": f.thumbnailUrl or None,
        "originalPath": f.originalPath or None,
        # Nested CloudAPI info
        "cloudName": f.CloudAPI.name if f.HasField("CloudAPI") else None,
        "cloudUserName": f.CloudAPI.userName if f.HasField("CloudAPI") else None,
    }

    # Populate detail properties if the proto has them
    try:
        if f.HasField("detailProperties") and f.detailProperties:
            dp = f.detailProperties
            result["fileCount"] = dp.totalFileCount
            result["folderCount"] = dp.totalFolderCount
            result["totalSize"] = dp.totalSize
    except (AttributeError, ValueError):
        pass  # detailProperties not supported or not set

    return result


# ---------------------------------------------------------------------------
# CD2 gRPC Client (ephemeral — one token per instance)
# ---------------------------------------------------------------------------
class CD2Client:
    """Lightweight gRPC client for CloudDrive2."""

    def __init__(
        self,
        host: Optional[str] = None,
        port: Optional[str] = None,
        username: Optional[str] = None,
        password: Optional[str] = None,
    ):
        cfg = _cd2_config()
        self._host = host or cfg["host"]
        self._port = port or cfg["port"]
        self._username = username or cfg["username"]
        self._password = password or cfg["password"]
        self._token: Optional[str] = None
        self._channel: Optional[grpc.Channel] = None
        self._stub: Optional[clouddrive_pb2_grpc.CloudDriveFileSrvStub] = None

    @property
    def target(self) -> str:
        return f"{self._host}:{self._port}"

    # ------------------------------------------------------------------
    # Connection management
    # ------------------------------------------------------------------
    def connect(self) -> None:
        """Create an insecure gRPC channel to the CD2 server."""
        if self._channel is None:
            self._channel = grpc.insecure_channel(self.target)
            self._stub = clouddrive_pb2_grpc.CloudDriveFileSrvStub(self._channel)
            logger.info("CD2 gRPC channel opened → %s", self.target)

    def close(self) -> None:
        """Tear down the gRPC channel."""
        if self._channel is not None:
            self._channel.close()
            self._channel = None
            self._stub = None
            self._token = None
            logger.info("CD2 gRPC channel closed.")

    # ------------------------------------------------------------------
    # Authentication
    # ------------------------------------------------------------------
    def login(self) -> str:
        """Obtain a JWT bearer token via GetToken RPC.

        Requires CD2_USERNAME / CD2_PASSWORD env vars to be set.
        Returns the token string.
        """
        if not self._username or not self._password:
            raise RuntimeError(
                "CD2 credentials not configured. "
                "Set CD2_USERNAME and CD2_PASSWORD environment variables."
            )

        self.connect()
        request = clouddrive_pb2.GetTokenRequest(
            userName=self._username,
            password=self._password,
        )
        response: clouddrive_pb2.JWTToken = self._stub.GetToken(request)

        if not response.success:
            raise PermissionError(
                f"CD2 login failed: {response.errorMessage or 'unknown error'}"
            )

        self._token = response.token
        logger.info("CD2 login successful — token obtained.")
        return self._token

    # ------------------------------------------------------------------
    # Authorised metadata
    # ------------------------------------------------------------------
    def _metadata(self):
        """Return gRPC metadata tuple with Bearer token."""
        if not self._token:
            raise RuntimeError("Not authenticated — call login() first.")
        return (("authorization", f"Bearer {self._token}"),)

    # ------------------------------------------------------------------
    # File-system operations
    # ------------------------------------------------------------------
    def get_sub_files(self, path: str, force_refresh: bool = False) -> list[dict]:
        """List all files/folders directly under *path*.

        Calls the server-streaming RPC ``GetSubFiles`` and collects all
        replies into a single list of dicts.

        Returns an empty list (and logs a warning) when the path does not
        exist, rather than letting the gRPC NOT_FOUND propagate as a 500.
        """
        if self._stub is None:
            self.connect()
            self.login()

        request = clouddrive_pb2.ListSubFileRequest(
            path=path,
            forceRefresh=force_refresh,
        )
        files: list[dict] = []
        try:
            for reply in self._stub.GetSubFiles(request, metadata=self._metadata()):
                for sf in reply.subFiles:
                    files.append(_file_to_dict(sf))
        except grpc.RpcError as e:
            if e.code() == grpc.StatusCode.NOT_FOUND:
                logger.warning("CD2 GetSubFiles('%s') → NOT_FOUND (path does not exist)", path)
                return []
            raise  # Re-raise unexpected gRPC errors

        logger.info("CD2 GetSubFiles('%s') → %d entries", path, len(files))
        return files

    def get_file_detail_properties(self, path: str) -> Optional[dict]:
        """Fetch detail properties for a single file/folder.

        Calls ``GetFileDetailProperties`` which returns ``totalFileCount``,
        ``totalFolderCount`` and ``totalSize`` for directories.
        Returns None on failure.
        """
        if self._stub is None:
            self.connect()
            self.login()

        request = clouddrive_pb2.FileRequest(path=path)
        try:
            resp: clouddrive_pb2.FileDetailProperties = (
                self._stub.GetFileDetailProperties(
                    request, metadata=self._metadata()
                )
            )
            return {
                "fileCount": resp.totalFileCount,
                "folderCount": resp.totalFolderCount,
                "totalSize": resp.totalSize,
            }
        except grpc.RpcError as e:
            logger.warning("CD2 GetFileDetailProperties('%s') failed: %s", path, e)
            return None

    def get_sub_files_with_details(
        self, path: str, force_refresh: bool = False
    ) -> list[dict]:
        """Like ``get_sub_files`` but also fetches ``GetFileDetailProperties``
        for every directory in the result, so that ``fileCount``, ``folderCount``
        and ``totalSize`` are populated.

        Non-directory entries are left as-is (the ``size`` field from
        ``GetSubFiles`` is already correct for regular files).
        """
        files = self.get_sub_files(path, force_refresh=force_refresh)

        for f in files:
            if not f["isDirectory"]:
                continue
            full_path = f["fullPathName"]
            details = self.get_file_detail_properties(full_path)
            if details:
                f["fileCount"] = details["fileCount"]
                f["folderCount"] = details["folderCount"]
                f["totalSize"] = details["totalSize"]

        return files

    def find_file_by_path(self, parent_path: str, path: str) -> Optional[dict]:
        """Look up a single file/folder by its parent path and relative path."""
        if self._stub is None:
            self.connect()
            self.login()

        request = clouddrive_pb2.FindFileByPathRequest(
            parentPath=parent_path,
            path=path,
        )
        try:
            result: clouddrive_pb2.CloudDriveFile = self._stub.FindFileByPath(
                request, metadata=self._metadata()
            )
            return _file_to_dict(result)
        except grpc.RpcError as e:
            logger.warning("CD2 FindFileByPath failed: %s", e)
            return None

    # ------------------------------------------------------------------
    # Create folder
    # ------------------------------------------------------------------
    def create_folder(self, parent_path: str, folder_name: str) -> dict:
        """Create a new folder under *parent_path*.

        Returns::

            {
              "success": True/False,
              "errorMessage": "...",       # only on failure
              "folder": { ... },           # created folder info
            }
        """
        if self._stub is None:
            self.connect()
            self.login()

        request = clouddrive_pb2.CreateFolderRequest(
            parentPath=parent_path,
            folderName=folder_name,
        )
        logger.info("CD2 CreateFolder: '%s' under '%s'", folder_name, parent_path)

        resp: clouddrive_pb2.CreateFolderResult = self._stub.CreateFolder(
            request, metadata=self._metadata()
        )

        result = {
            "success": resp.result.success,
            "folder": _file_to_dict(resp.folderCreated) if resp.HasField("folderCreated") else None,
        }
        if not resp.result.success:
            result["errorMessage"] = resp.result.errorMessage or "unknown error"
            logger.warning("CD2 CreateFolder failed: %s", result["errorMessage"])
        else:
            logger.info("CD2 CreateFolder succeeded: '%s'", folder_name)

        return result

    # ------------------------------------------------------------------
    # Move operations
    # ------------------------------------------------------------------
    @staticmethod
    def _sanitize_path(path: str) -> str:
        """Strip trailing slash(es) from a path, preserving the root ``/``."""
        if not path:
            return path
        stripped = path.rstrip('/')
        return stripped if stripped else '/'

    def move_files(
        self,
        source_paths: list[str],
        dest_path: str,
        conflict_policy: int = 1,  # 0=Overwrite, 1=Rename (default), 2=Skip
    ) -> dict:
        """Move files/folders to a destination directory.

        Parameters
        ----------
        source_paths : list[str]
            Full paths of the files/folders to move.
        dest_path : str
            Destination parent directory path.
        conflict_policy : int
            0 = Overwrite, 1 = Rename (auto-rename on conflict), 2 = Skip.

        Returns::

            {
              "success": True/False,
              "errorMessage": "...",       # only on failure
              "resultFilePaths": [...],    # affected paths
            }
        """
        if self._stub is None:
            self.connect()
            self.login()

        # ---- sanitise trailing slashes (CD2 is sensitive to them) ----
        clean_sources = [self._sanitize_path(p) for p in source_paths]
        clean_dest = self._sanitize_path(dest_path)

        conflict_map = {
            0: clouddrive_pb2.MoveFileRequest.Overwrite,
            1: clouddrive_pb2.MoveFileRequest.Rename,
            2: clouddrive_pb2.MoveFileRequest.Skip,
        }
        policy = conflict_map.get(conflict_policy, clouddrive_pb2.MoveFileRequest.Rename)

        request = clouddrive_pb2.MoveFileRequest(
            theFilePaths=clean_sources,
            destPath=clean_dest,
            conflictPolicy=policy,
        )
        logger.info(
            "CD2 MoveFile → %d paths to '%s' (conflictPolicy=%d)",
            len(clean_sources), clean_dest, conflict_policy,
        )
        for i, p in enumerate(clean_sources):
            logger.info("  [%d] %s", i + 1, p)

        # ---- invoke with NOT_FOUND retry (exponential backoff) ----
        # CD2's directory index can lag behind the cloud filesystem after
        # a write/delete on another node.  A NOT_FOUND is often transient
        # (stale cache).  Use exponential backoff to give the index time
        # to catch up, rather than silently assuming success — which masks
        # real failures like a genuinely missing target directory.
        # Fixed-interval backoff: 10s → 30s → 1min → 2min
        retry_delays = [10, 30, 60, 120]  # seconds
        max_retries = len(retry_delays)
        last_error = None

        for attempt in range(max_retries + 1):
            try:
                resp: clouddrive_pb2.FileOperationResult = self._stub.MoveFile(
                    request, metadata=self._metadata()
                )
                break  # success — exit retry loop
            except grpc.RpcError as e:
                last_error = e
                if e.code() != grpc.StatusCode.NOT_FOUND:
                    raise  # non-NOT_FOUND error — don't retry

                if attempt < max_retries:
                    delay = retry_delays[attempt]
                    logger.warning(
                        "CD2 MoveFile NOT_FOUND (attempt %d/%d), "
                        "retrying in %ds…  error=%s",
                        attempt + 1, max_retries + 1, delay,
                        e.details(),
                    )
                    time.sleep(delay)
                else:
                    # All retries exhausted — surface the real error
                    logger.error(
                        "CD2 MoveFile NOT_FOUND after %d retries — "
                        "target may not exist.  error=%s",
                        max_retries + 1, e.details(),
                    )
                    return {
                        "success": False,
                        "errorMessage": (
                            f"移动失败: CD2 返回 NOT_FOUND "
                            f"(已重试 {max_retries + 1} 次). "
                            f"{e.details()}"
                        ),
                        "resultFilePaths": [],
                    }

        result = {
            "success": resp.success,
            "resultFilePaths": list(resp.resultFilePaths),
        }
        if not resp.success:
            result["errorMessage"] = resp.errorMessage or "unknown error"
            logger.warning("CD2 move failed: %s", result["errorMessage"])
        else:
            logger.info(
                "CD2 move succeeded — %d paths affected",
                len(result["resultFilePaths"]),
            )

        return result

    # ------------------------------------------------------------------
    # Delete operations
    # ------------------------------------------------------------------
    def delete_files(
        self,
        paths: list[str],
        permanent: bool = False,
    ) -> dict:
        """Delete files/folders by their full paths.

        - ``permanent=False`` → calls ``DeleteFiles`` (move to recycle bin).
        - ``permanent=True``  → calls ``DeleteFilesPermanently`` (only supported
          by aliyundrive; other clouds may return an error).

        Returns::

            {
              "success": True/False,
              "errorMessage": "...",       # only on failure
              "resultFilePaths": [...],    # paths that were deleted
            }
        """
        if self._stub is None:
            self.connect()
            self.login()

        request = clouddrive_pb2.MultiFileRequest(path=paths)

        if permanent:
            logger.info(
                "CD2 DeleteFilesPermanently → %d paths: %s",
                len(paths), paths,
            )
            resp: clouddrive_pb2.FileOperationResult = (
                self._stub.DeleteFilesPermanently(
                    request, metadata=self._metadata()
                )
            )
        else:
            logger.info(
                "CD2 DeleteFiles (recycle) → %d paths: %s",
                len(paths), paths,
            )
            resp: clouddrive_pb2.FileOperationResult = (
                self._stub.DeleteFiles(
                    request, metadata=self._metadata()
                )
            )

        result = {
            "success": resp.success,
            "resultFilePaths": list(resp.resultFilePaths),
        }
        if not resp.success:
            result["errorMessage"] = resp.errorMessage or "unknown error"
            logger.warning(
                "CD2 delete failed (permanent=%s): %s",
                permanent, result["errorMessage"],
            )
        else:
            logger.info(
                "CD2 delete succeeded (permanent=%s) — %d paths affected",
                permanent, len(result["resultFilePaths"]),
            )

        return result

    # ------------------------------------------------------------------
    # Convenience: fetch both target directories at once
    # ------------------------------------------------------------------
    def fetch_both_directories(
        self,
        media_dir: Optional[str] = None,
        organized_dir: Optional[str] = None,
        include_details: bool = False,
        media_include_details: Optional[bool] = None,
        organized_include_details: Optional[bool] = None,
    ) -> dict:
        """Return a dict with ``media`` and ``organized`` file lists.

        When *include_details* is True, each directory in the result will
        be enriched with ``fileCount``, ``folderCount`` and ``totalSize``
        via additional ``GetFileDetailProperties`` RPC calls.

        Per-side flags (``media_include_details`` / ``organized_include_details``)
        override *include_details* when provided, allowing independent control
        of stats fetching for each side.
        """
        cfg = _cd2_config()
        if media_dir is None:
            media_dir = cfg["media_dir"]
        if organized_dir is None:
            organized_dir = cfg["organized_dir"]

        media_fetcher = self.get_sub_files_with_details if (
            media_include_details if media_include_details is not None else include_details
        ) else self.get_sub_files

        organized_fetcher = self.get_sub_files_with_details if (
            organized_include_details if organized_include_details is not None else include_details
        ) else self.get_sub_files

        # Fetch each side independently — a missing directory on one side
        # must not crash the other side's result.
        media_result: list[dict] = []
        organized_result: list[dict] = []
        errors: list[str] = []

        try:
            media_result = media_fetcher(media_dir)
        except Exception as e:
            logger.warning("CD2 fetch_both: media side failed for '%s': %s", media_dir, e)
            errors.append(f"media: {e}")

        try:
            organized_result = organized_fetcher(organized_dir)
        except Exception as e:
            logger.warning("CD2 fetch_both: organized side failed for '%s': %s", organized_dir, e)
            errors.append(f"organized: {e}")

        result = {
            "media": media_result,
            "organized": organized_result,
        }
        if errors:
            result["errors"] = errors
        return result

    # ------------------------------------------------------------------
    # Context-manager support
    # ------------------------------------------------------------------
    def __enter__(self):
        self.connect()
        self.login()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()
        return False


# ---------------------------------------------------------------------------
# Module-level convenience (keeps the channel alive for repeated calls)
# ---------------------------------------------------------------------------
_client: Optional[CD2Client] = None


def get_client() -> CD2Client:
    """Return a singleton CD2Client, logging in automatically."""
    global _client
    if _client is None:
        _client = CD2Client()
        _client.connect()
        _client.login()
    return _client


def close_client() -> None:
    """Close the singleton client and release resources."""
    global _client
    if _client is not None:
        _client.close()
        _client = None
