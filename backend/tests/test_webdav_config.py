"""WebDAV 图片缓存配置解析测试 — env 优先、config.json 兜底、未配置判空。"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest
from config import settings


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    for k in ("WEBDAV_BASE_URL", "WEBDAV_USERNAME", "WEBDAV_PASSWORD", "WEBDAV_ROOT_PATH"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setattr(settings, "load_config", lambda: {})


def test_env_overrides_config(monkeypatch):
    monkeypatch.setenv("WEBDAV_BASE_URL", "http://env.example/dav")
    monkeypatch.setenv("WEBDAV_USERNAME", "u")
    monkeypatch.setenv("WEBDAV_PASSWORD", "p")
    monkeypatch.setattr(settings, "load_config", lambda: {"webdav_base_url": "http://cfg.example"})
    cfg = settings.get_webdav_config()
    assert cfg["base_url"] == "http://env.example/dav"
    assert cfg["username"] == "u"


def test_config_fallback_when_no_env(monkeypatch):
    monkeypatch.setattr(settings, "load_config",
                        lambda: {"webdav_base_url": "http://cfg.example", "webdav_root_path": "/dav"})
    cfg = settings.get_webdav_config()
    assert cfg["base_url"] == "http://cfg.example"
    assert cfg["root_path"] == "/dav"


def test_empty_when_unconfigured():
    cfg = settings.get_webdav_config()
    assert cfg == {"base_url": "", "username": "", "password": "", "root_path": "",
                   "media_root": "library", "people_root": "library"}


def test_roots_configurable_via_config_file(monkeypatch):
    monkeypatch.setattr(settings, "load_config",
                        lambda: {"webdav_media_root": "movies", "webdav_people_root": "actors"})
    cfg = settings.get_webdav_config()
    assert cfg["media_root"] == "movies"
    assert cfg["people_root"] == "actors"


def test_roots_env_overrides_config(monkeypatch):
    monkeypatch.setenv("WEBDAV_MEDIA_ROOT", "/media")
    monkeypatch.setattr(settings, "load_config", lambda: {"webdav_media_root": "movies"})
    assert settings.get_webdav_config()["media_root"] == "/media"   # env 原样，strip 在拼接层
