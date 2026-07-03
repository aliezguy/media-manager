"""
Path utility functions for Emby ↔ CD2 conversion and webhook data extraction.
"""

import re
from typing import Optional

# Emby mounts the 115 cloud storage at this path
EMBY_PREFIX = "/volume3/emby影院/115网盘_3588/"
# CD2 sees the same cloud storage at this path
CD2_MEDIA_PREFIX = "/80003588/emby库/"

# Regex to extract TMDB ID from paths like "剧名(2026) {tmdb=289271}"
_TMDB_ID_RE = re.compile(r"\{tmdb=(\d+)\}")

# Regex to parse season/episode info from Description like "S01 E01-E24"
_DESC_SE_RE = re.compile(r"S(\d{1,2})\s+E(\d{1,3})-E(\d{1,3})")

# Regex to extract TmdbId from Description like "TmdbId: 289271"
_DESC_TMDB_RE = re.compile(r"TmdbId:\s*(\d+)")


def extract_tmdb_id_from_path(path: str) -> Optional[int]:
    """Extract TMDB ID from an Emby or CD2 path.

    Example: "/volume3/.../翘楚(2026) {tmdb=289271}" → 289271
    """
    if not path:
        return None
    m = _TMDB_ID_RE.search(path)
    return int(m.group(1)) if m else None


def extract_tmdb_id_from_description(description: str) -> Optional[int]:
    """Extract TMDB ID from Emby webhook Description field.

    Example: "S01 E01-E24\\n\\nTmdbId: 289271" → 289271
    """
    if not description:
        return None
    m = _DESC_TMDB_RE.search(description)
    return int(m.group(1)) if m else None


def extract_tmdb_id_from_payload(payload: dict) -> Optional[int]:
    """Extract TMDB ID from an Emby webhook payload using multiple strategies.

    Priority:
    1. Item.ProviderIds.Tmdb
    2. Item.Path regex (fallback)
    3. Description TmdbId line (fallback)
    """
    # Priority 1: ProviderIds
    item = payload.get("Item", {})
    provider_ids = item.get("ProviderIds", {})
    tmdb_str = provider_ids.get("Tmdb")
    if tmdb_str:
        try:
            return int(tmdb_str)
        except (ValueError, TypeError):
            pass

    # Priority 2: Path regex
    path = item.get("Path", "")
    tmdb_id = extract_tmdb_id_from_path(path)
    if tmdb_id is not None:
        return tmdb_id

    # Priority 3: Description TmdbId
    desc = payload.get("Description", "")
    tmdb_id = extract_tmdb_id_from_description(desc)
    if tmdb_id is not None:
        return tmdb_id

    return None


def emby_path_to_cd2_path(emby_path: str) -> str:
    """Convert an Emby library path to the corresponding CD2 media library path.

    /volume3/emby影院/115网盘_3588/电视剧/国产剧/2026/翘楚(2026) {tmdb=289271}
        → /80003588/emby库/电视剧/国产剧/2026/翘楚(2026) {tmdb=289271}
    """
    if emby_path.startswith(EMBY_PREFIX):
        return emby_path.replace(EMBY_PREFIX, CD2_MEDIA_PREFIX, 1)
    return emby_path


def extract_season_episodes_from_description(description: str) -> Optional[dict]:
    """Parse season/episode range from Emby library.new Description.

    Example: "S01 E01-E24\\n\\nTmdbId: 289271" →
        {"season": 1, "start_episode": 1, "end_episode": 24, "episode_count": 24}
    """
    if not description:
        return None
    m = _DESC_SE_RE.search(description)
    if not m:
        return None
    season = int(m.group(1))
    start_ep = int(m.group(2))
    end_ep = int(m.group(3))
    return {
        "season": season,
        "start_episode": start_ep,
        "end_episode": end_ep,
        "episode_count": end_ep - start_ep + 1,
    }
