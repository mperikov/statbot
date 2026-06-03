"""Агрегация убийств/смертей/KD и макс. дистанции по кланам и игрокам."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from killfeed_parser import KillEvent, clan_tokens, normalize_clan_key, normalize_steam_key


@dataclass
class ClanStats:
    display_name: str
    kills: int = 0
    deaths: int = 0
    max_distance_m: float = 0.0

    @property
    def kd(self) -> float:
        if self.deaths == 0:
            return float(self.kills)
        return self.kills / self.deaths


@dataclass
class PlayerStats:
    steam_key: str
    display_name: str
    kills: int = 0
    deaths: int = 0
    max_distance_m: float = 0.0

    @property
    def kd(self) -> float:
        if self.deaths == 0:
            return float(self.kills)
        return self.kills / self.deaths


class RankingEntry(Protocol):
    display_name: str
    kills: int
    deaths: int
    max_distance_m: float

    @property
    def kd(self) -> float: ...


class StatsAggregator:
    def __init__(self) -> None:
        self._clans: dict[str, ClanStats] = {}
        self._players: dict[str, PlayerStats] = {}

    def _get_or_create_clan(self, token: str) -> ClanStats:
        key = normalize_clan_key(token)
        existing = self._clans.get(key)
        if existing is not None:
            return existing
        stats = ClanStats(display_name=token.strip())
        self._clans[key] = stats
        return stats

    def _get_or_create_player(self, steam_key: str, nickname: str) -> PlayerStats:
        existing = self._players.get(steam_key)
        if existing is not None:
            existing.display_name = nickname.strip() or existing.display_name
            return existing
        stats = PlayerStats(
            steam_key=steam_key,
            display_name=nickname.strip() or steam_key,
        )
        self._players[steam_key] = stats
        return stats

    def apply_kill(self, event: KillEvent) -> None:
        for token in clan_tokens(event.killer_nick):
            clan = self._get_or_create_clan(token)
            clan.kills += 1
            if event.distance_m > clan.max_distance_m:
                clan.max_distance_m = event.distance_m

        for token in clan_tokens(event.victim_nick):
            clan = self._get_or_create_clan(token)
            clan.deaths += 1

        killer_steam = normalize_steam_key(event.killer_steam)
        if killer_steam is not None:
            killer = self._get_or_create_player(killer_steam, event.killer_nick)
            killer.kills += 1
            if event.distance_m > killer.max_distance_m:
                killer.max_distance_m = event.distance_m

        victim_steam = normalize_steam_key(event.victim_steam)
        if victim_steam is not None:
            victim = self._get_or_create_player(victim_steam, event.victim_nick)
            victim.deaths += 1

    def top_clans_by_kills(self, limit: int = 7) -> list[ClanStats]:
        ranked = sorted(
            self._clans.values(),
            key=lambda c: (-c.kills, -c.kd, c.display_name.casefold()),
        )
        return [c for c in ranked if c.kills > 0][:limit]

    def top_players_by_kills(self, limit: int = 7) -> list[PlayerStats]:
        ranked = sorted(
            self._players.values(),
            key=lambda p: (-p.kills, -p.kd, p.display_name.casefold()),
        )
        return [p for p in ranked if p.kills > 0][:limit]

    def clear(self) -> None:
        self._clans.clear()
        self._players.clear()

    def to_dict(self) -> dict[str, Any]:
        return {
            "clans": {
                key: {
                    "display_name": stats.display_name,
                    "kills": stats.kills,
                    "deaths": stats.deaths,
                    "max_distance_m": stats.max_distance_m,
                }
                for key, stats in self._clans.items()
            },
            "players": {
                key: {
                    "steam_key": stats.steam_key,
                    "display_name": stats.display_name,
                    "kills": stats.kills,
                    "deaths": stats.deaths,
                    "max_distance_m": stats.max_distance_m,
                }
                for key, stats in self._players.items()
            },
        }

    def load_dict(self, data: dict[str, Any]) -> None:
        self.clear()
        clans_raw = data.get("clans")
        if isinstance(clans_raw, dict):
            for key, raw in clans_raw.items():
                if not isinstance(raw, dict):
                    continue
                display = raw.get("display_name")
                if not isinstance(display, str) or not display.strip():
                    continue
                try:
                    kills = int(raw.get("kills", 0))
                    deaths = int(raw.get("deaths", 0))
                    max_distance = float(raw.get("max_distance_m", 0.0))
                except (TypeError, ValueError):
                    continue
                self._clans[str(key)] = ClanStats(
                    display_name=display.strip(),
                    kills=max(0, kills),
                    deaths=max(0, deaths),
                    max_distance_m=max(0.0, max_distance),
                )

        players_raw = data.get("players")
        if isinstance(players_raw, dict):
            for key, raw in players_raw.items():
                if not isinstance(raw, dict):
                    continue
                steam_key = raw.get("steam_key")
                display = raw.get("display_name")
                if not isinstance(steam_key, str) or not steam_key.strip():
                    steam_key = str(key)
                if not isinstance(display, str) or not display.strip():
                    continue
                try:
                    kills = int(raw.get("kills", 0))
                    deaths = int(raw.get("deaths", 0))
                    max_distance = float(raw.get("max_distance_m", 0.0))
                except (TypeError, ValueError):
                    continue
                self._players[str(key)] = PlayerStats(
                    steam_key=steam_key.strip(),
                    display_name=display.strip(),
                    kills=max(0, kills),
                    deaths=max(0, deaths),
                    max_distance_m=max(0.0, max_distance),
                )


class GuildStatsState:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.aggregator = StatsAggregator()
        self.last_message_id: int | None = None
        self.stats_message_id: int | None = None
        self.player_stats_message_id: int | None = None

    @staticmethod
    def load_all(path: Path) -> dict[str, dict[str, Any]]:
        if not path.exists():
            return {}
        try:
            with path.open("r", encoding="utf-8") as f:
                data = json.load(f)
        except (OSError, json.JSONDecodeError):
            return {}
        guilds = data.get("guilds") if isinstance(data, dict) else None
        return guilds if isinstance(guilds, dict) else {}

    @staticmethod
    def save_all(path: Path, guilds: dict[str, dict[str, Any]]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w", encoding="utf-8") as f:
            json.dump({"guilds": guilds}, f, ensure_ascii=False, indent=2)

    def to_guild_blob(self) -> dict[str, Any]:
        blob: dict[str, Any] = {
            "stats": self.aggregator.to_dict(),
        }
        if self.last_message_id is not None:
            blob["last_message_id"] = self.last_message_id
        if self.stats_message_id is not None:
            blob["stats_message_id"] = self.stats_message_id
        if self.player_stats_message_id is not None:
            blob["player_stats_message_id"] = self.player_stats_message_id
        return blob

    def load_guild_blob(self, blob: dict[str, Any]) -> None:
        stats_raw = blob.get("stats")
        if isinstance(stats_raw, dict):
            self.aggregator.load_dict(stats_raw)
        last_id = blob.get("last_message_id")
        if isinstance(last_id, int):
            self.last_message_id = last_id
        msg_id = blob.get("stats_message_id")
        if isinstance(msg_id, int):
            self.stats_message_id = msg_id
        player_msg_id = blob.get("player_stats_message_id")
        if isinstance(player_msg_id, int):
            self.player_stats_message_id = player_msg_id
