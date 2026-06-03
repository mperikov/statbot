"""Привязка каналов killfeed → статистика по guild."""

from __future__ import annotations

import json
import os
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

from count_from import deserialize_count_from, serialize_count_from


CONFIG_PATH = Path(os.getenv("CHANNEL_CONFIG_PATH", "channel_config.json"))


def load_channel_config() -> dict[str, dict[str, Any]]:
    if not CONFIG_PATH.exists():
        return {}
    with CONFIG_PATH.open("r", encoding="utf-8") as f:
        data = json.load(f)
    if not isinstance(data, dict):
        return {}

    result: dict[str, dict[str, Any]] = {}
    for guild_id, channels in data.items():
        if not isinstance(channels, dict):
            continue
        try:
            entry: dict[str, Any] = {
                "source_channel_id": int(channels["source_channel_id"]),
                "stats_channel_id": int(channels["stats_channel_id"]),
            }
        except (KeyError, TypeError, ValueError):
            continue
        count_from_raw = channels.get("count_from_utc")
        if isinstance(count_from_raw, str) and count_from_raw.strip():
            entry["count_from_utc"] = count_from_raw.strip()
        result[str(guild_id)] = entry
    return result


def save_channel_config(config: dict[str, dict[str, Any]]) -> None:
    with CONFIG_PATH.open("w", encoding="utf-8") as f:
        json.dump(config, f, ensure_ascii=False, indent=2)


def get_guild_entry(guild_id: int) -> Optional[dict[str, Any]]:
    return load_channel_config().get(str(guild_id))


def get_channels(guild_id: int) -> Optional[dict[str, int]]:
    entry = get_guild_entry(guild_id)
    if entry is None:
        return None
    return {
        "source_channel_id": int(entry["source_channel_id"]),
        "stats_channel_id": int(entry["stats_channel_id"]),
    }


def get_count_from_utc(guild_id: int) -> Optional[datetime]:
    entry = get_guild_entry(guild_id)
    if entry is None:
        return None
    raw = entry.get("count_from_utc")
    if not isinstance(raw, str):
        return None
    return deserialize_count_from(raw)


def set_count_from_utc(guild_id: int, when_utc: datetime) -> None:
    config = load_channel_config()
    entry = dict(config.get(str(guild_id), {}))
    entry["count_from_utc"] = serialize_count_from(when_utc)
    config[str(guild_id)] = entry
    save_channel_config(config)


def clear_count_from_utc(guild_id: int) -> bool:
    config = load_channel_config()
    entry = config.get(str(guild_id))
    if not isinstance(entry, dict) or "count_from_utc" not in entry:
        return False
    del entry["count_from_utc"]
    config[str(guild_id)] = entry
    save_channel_config(config)
    return True


def set_channels(guild_id: int, source_channel_id: int, stats_channel_id: int) -> None:
    config = load_channel_config()
    entry = dict(config.get(str(guild_id), {}))
    entry["source_channel_id"] = source_channel_id
    entry["stats_channel_id"] = stats_channel_id
    config[str(guild_id)] = entry
    save_channel_config(config)


def remove_channels(guild_id: int) -> bool:
    config = load_channel_config()
    existed = str(guild_id) in config
    if existed:
        del config[str(guild_id)]
        save_channel_config(config)
    return existed
