"""Парсинг строк killfeed и извлечение кланов из никнейма."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional


# [nick](<url>) killed [nick](<url>) with ... from N meters
KILL_LINE_RE = re.compile(
    r"\[(?P<killer_nick>[^\]]+)\]\s*\(\s*<?(?P<killer_steam>[^>)]+)>?\s*\)\s+killed\s+"
    r"\[(?P<victim_nick>[^\]]+)\]\s*\(\s*<?(?P<victim_steam>[^>)]+)>?\s*\)\s+with\s+"
    r"(?P<gun>.+?)\s+from\s+(?P<distance>[\d.,]+)\s+meters",
    re.IGNORECASE,
)


@dataclass(frozen=True, slots=True)
class KillEvent:
    killer_nick: str
    killer_steam: str
    victim_nick: str
    victim_steam: str
    gun: str
    distance_m: float


def parse_distance(raw: str) -> Optional[float]:
    cleaned = raw.strip().replace(",", ".")
    try:
        value = float(cleaned)
    except ValueError:
        return None
    if value < 0:
        return None
    return value


def normalize_clan_key(token: str) -> str:
    return token.casefold()


_STEAM_PROFILE_ID_RE = re.compile(
    r"steamcommunity\.com/profiles/(?P<id>\d+)",
    re.IGNORECASE,
)
_STEAM_VANITY_ID_RE = re.compile(
    r"steamcommunity\.com/id/(?P<id>[^/\s?#]+)",
    re.IGNORECASE,
)


def normalize_steam_key(raw: str) -> Optional[str]:
    """Ключ игрока из Steam-ссылки или числового Steam64."""
    cleaned = (raw or "").strip().rstrip("/")
    if not cleaned:
        return None

    match = _STEAM_PROFILE_ID_RE.search(cleaned)
    if match:
        return f"steam64:{match.group('id')}"

    match = _STEAM_VANITY_ID_RE.search(cleaned)
    if match:
        vanity = match.group("id").strip().rstrip("/")
        if vanity:
            return f"vanity:{vanity.casefold()}"

    if cleaned.isdigit():
        return f"steam64:{cleaned}"

    return None


_CLAN_TOKEN_RE = re.compile(r"\.\.\.|[^\s.^]+")


def clan_tokens(nickname: str) -> list[str]:
    """Слова ника по пробелам/точкам/^; отдельно литерал «...» как клан."""
    seen: set[str] = set()
    tokens: list[str] = []
    for match in _CLAN_TOKEN_RE.finditer(nickname.strip()):
        word = match.group(0)
        if not word:
            continue
        key = normalize_clan_key(word)
        if key in seen:
            continue
        seen.add(key)
        tokens.append(word)
    return tokens


def parse_kill_message(content: str) -> Optional[KillEvent]:
    if not content:
        return None
    match = KILL_LINE_RE.search(content.strip())
    if match is None:
        return None
    distance = parse_distance(match.group("distance"))
    if distance is None:
        return None
    return KillEvent(
        killer_nick=match.group("killer_nick").strip(),
        killer_steam=match.group("killer_steam").strip(),
        victim_nick=match.group("victim_nick").strip(),
        victim_steam=match.group("victim_steam").strip(),
        gun=match.group("gun").strip(),
        distance_m=distance,
    )
