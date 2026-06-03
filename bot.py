"""Discord-бот: статистика кланов по каналу killfeed."""

from __future__ import annotations

import asyncio
import io
import logging
import os
from pathlib import Path
from typing import Any

import discord
from discord import app_commands
from discord.ext import commands, tasks
from dotenv import load_dotenv

import channel_config
from count_from import (
    DEFAULT_TIMEZONE,
    format_count_from_display,
    message_counts_from,
    parse_count_from,
)
from killfeed_parser import parse_kill_message
from stats_renderer import (
    TOP_CLAN_LIMIT,
    TOP_PLAYER_LIMIT,
    render_player_stats_image,
    render_stats_image,
)
from stats_store import GuildStatsState, print_top_clans_console


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("statbot")

_REPO_ROOT = Path(__file__).resolve().parent
load_dotenv(_REPO_ROOT / ".env")

STATS_DATA_PATH = Path(os.getenv("STATS_DATA_PATH", str(_REPO_ROOT / "stats_data.json")))
SYNC_GUILD_ID = os.getenv("SYNC_GUILD_ID")
KILLFEED_POLL_INTERVAL_SEC = max(1, int(os.getenv("KILLFEED_POLL_INTERVAL_SEC", "2")))
STATS_PUBLISH_DEBOUNCE_SEC = max(1.0, float(os.getenv("STATS_PUBLISH_DEBOUNCE_SEC", "5")))
STAT_CONSOLE_TOP_CLANS = int(os.getenv("STAT_CONSOLE_TOP_CLANS", "20"))


def _get_required_env(name: str) -> str:
    value = os.getenv(name)
    if not value:
        env_path = _REPO_ROOT / ".env"
        raise RuntimeError(
            f"Environment variable '{name}' is required. "
            f"Copy {_REPO_ROOT / '.env.example'} to {env_path} and set {name}=..."
        )
    return value


TOKEN = _get_required_env("BOT_TOKEN")

intents = discord.Intents.default()
intents.guilds = True
intents.message_content = True

bot = commands.Bot(command_prefix="!", intents=intents)

_guild_states: dict[int, GuildStatsState] = {}
_guild_locks: dict[int, asyncio.Lock] = {}
_publish_tasks: dict[int, asyncio.Task] = {}
_publish_locks: dict[int, asyncio.Lock] = {}
_persist_lock = asyncio.Lock()


def _guild_lock(guild_id: int) -> asyncio.Lock:
    lock = _guild_locks.get(guild_id)
    if lock is None:
        lock = asyncio.Lock()
        _guild_locks[guild_id] = lock
    return lock


def _get_guild_state(guild_id: int) -> GuildStatsState:
    state = _guild_states.get(guild_id)
    if state is None:
        state = GuildStatsState(STATS_DATA_PATH)
        _guild_states[guild_id] = state
    return state


def _load_persisted_states() -> None:
    guilds = GuildStatsState.load_all(STATS_DATA_PATH)
    for guild_id_str, blob in guilds.items():
        try:
            guild_id = int(guild_id_str)
        except ValueError:
            continue
        if not isinstance(blob, dict):
            continue
        state = _get_guild_state(guild_id)
        state.load_guild_blob(blob)


def _period_note(guild_id: int) -> str | None:
    count_from = channel_config.get_count_from_utc(guild_id)
    if count_from is None:
        return None
    return f"Учёт с {format_count_from_display(count_from)}"


def _should_count_message(message: discord.Message, guild_id: int) -> bool:
    return message_counts_from(message.created_at, channel_config.get_count_from_utc(guild_id))


def _publish_lock(guild_id: int) -> asyncio.Lock:
    lock = _publish_locks.get(guild_id)
    if lock is None:
        lock = asyncio.Lock()
        _publish_locks[guild_id] = lock
    return lock


async def _safe_interaction_reply(
    interaction: discord.Interaction,
    content: str,
    *,
    ephemeral: bool = True,
) -> None:
    try:
        if interaction.response.is_done():
            await interaction.followup.send(content, ephemeral=ephemeral)
        else:
            await interaction.response.send_message(content, ephemeral=ephemeral)
    except discord.NotFound:
        logger.warning("Interaction expired before response could be sent.")
    except discord.HTTPException as exc:
        if exc.code == 40060:
            logger.warning("Interaction already acknowledged.")
            return
        raise


async def _save_persisted_states() -> None:
    async with _persist_lock:
        guilds: dict[str, dict[str, Any]] = {}
        for guild_id, state in _guild_states.items():
            guilds[str(guild_id)] = state.to_guild_blob()
        GuildStatsState.save_all(STATS_DATA_PATH, guilds)


def _message_text(message: discord.Message) -> str:
    content = (message.content or "").strip()
    if content:
        return content
    for embed in message.embeds:
        if embed.description:
            return embed.description.strip()
    return ""


async def _apply_killfeed_message(
    message: discord.Message,
    guild_id: int,
    *,
    update_stats: bool,
    persist: bool = True,
) -> bool:
    """Учитывает одно сообщение killfeed. Возвращает True, если добавлен kill."""
    state = _get_guild_state(guild_id)

    if state.last_message_id is not None and message.id <= state.last_message_id:
        return False

    if bot.user and message.author.id == bot.user.id:
        if state.last_message_id is None or message.id > state.last_message_id:
            state.last_message_id = message.id
        return False

    counted_kill = False
    if _should_count_message(message, guild_id):
        event = parse_kill_message(_message_text(message))
        if event is not None:
            state.aggregator.apply_kill(event)
            counted_kill = True

    if state.last_message_id is None or message.id > state.last_message_id:
        state.last_message_id = message.id

    if counted_kill and update_stats:
        _schedule_publish_stats(guild_id)
        logger.info(
            "Killfeed +1 guild=%s msg=%s (stats publish scheduled)",
            guild_id,
            message.id,
        )

    if persist:
        await _save_persisted_states()

    return counted_kill


async def _process_channel_history(
    channel: discord.TextChannel,
    state: GuildStatsState,
    guild_id: int,
    *,
    full_rebuild: bool,
) -> int:
    """Сканирует историю killfeed; возвращает число учтённых kill-сообщений."""
    processed = 0
    if full_rebuild:
        state.aggregator.clear()
        state.last_message_id = None

    after_obj: discord.Object | None = None
    if not full_rebuild and state.last_message_id is not None:
        after_obj = discord.Object(id=state.last_message_id)

    async for message in channel.history(limit=None, oldest_first=True, after=after_obj):
        if await _apply_killfeed_message(
            message, guild_id, update_stats=False, persist=False
        ):
            processed += 1

    return processed


async def _catchup_new_killfeed(guild: discord.Guild) -> int:
    channels = channel_config.get_channels(guild.id)
    if channels is None:
        return 0

    source = guild.get_channel(channels["source_channel_id"])
    if not isinstance(source, discord.TextChannel):
        return 0

    state = _get_guild_state(guild.id)
    if state.last_message_id is None:
        return 0

    processed = 0
    async with _guild_lock(guild.id):
        async for message in source.history(
            limit=None,
            oldest_first=True,
            after=discord.Object(id=state.last_message_id),
        ):
            if await _apply_killfeed_message(
                message, guild.id, update_stats=False, persist=False
            ):
                processed += 1

        if processed > 0:
            await _save_persisted_states()
            await _publish_stats(guild, state, force=True)
            logger.info("Killfeed catch-up guild=%s new_kills=%s", guild.id, processed)

    return processed


async def _publish_stats_message(
    stats_channel: discord.TextChannel,
    *,
    message_id: int | None,
    image_buffer: io.BytesIO,
    filename: str,
) -> int | None:
    image_buffer.seek(0)
    stats_file = discord.File(fp=image_buffer, filename=filename)
    edit_kwargs: dict[str, Any] = {"content": "", "attachments": [stats_file]}

    if message_id is not None:
        try:
            msg = await stats_channel.fetch_message(message_id)
            await msg.edit(**edit_kwargs)
            return message_id
        except discord.NotFound:
            pass
        except discord.HTTPException as exc:
            if exc.status == 429:
                logger.warning("Rate limited editing stats message in channel %s", stats_channel.id)
            else:
                logger.exception("Failed to edit stats message in channel %s", stats_channel.id)
            return message_id

    try:
        sent = await stats_channel.send(file=stats_file)
        return sent.id
    except discord.HTTPException:
        logger.exception("Failed to send stats message in channel %s", stats_channel.id)
        return message_id


async def _do_publish_stats(guild: discord.Guild, state: GuildStatsState) -> None:
    channels = channel_config.get_channels(guild.id)
    if channels is None:
        return

    stats_channel = guild.get_channel(channels["stats_channel_id"])
    if not isinstance(stats_channel, discord.TextChannel):
        logger.warning("Stats channel %s not found for guild %s", channels["stats_channel_id"], guild.id)
        return

    clans = state.aggregator.top_clans_by_kills(TOP_CLAN_LIMIT)
    players = state.aggregator.top_players_by_kills(TOP_PLAYER_LIMIT)
    print_top_clans_console(
        state.aggregator,
        limit=STAT_CONSOLE_TOP_CLANS,
        guild_id=guild.id,
    )
    try:
        clan_buffer = render_stats_image(clans)
        player_buffer = render_player_stats_image(players)
    except OSError as exc:
        logger.exception("Failed to render stats image for guild %s: %s", guild.id, exc)
        return

    async with _publish_lock(guild.id):
        state.stats_message_id = await _publish_stats_message(
            stats_channel,
            message_id=state.stats_message_id,
            image_buffer=clan_buffer,
            filename="clan_stats.jpg",
        )
        state.player_stats_message_id = await _publish_stats_message(
            stats_channel,
            message_id=state.player_stats_message_id,
            image_buffer=player_buffer,
            filename="player_stats.jpg",
        )


async def _debounced_publish_worker(guild_id: int) -> None:
    try:
        await asyncio.sleep(STATS_PUBLISH_DEBOUNCE_SEC)
        guild = bot.get_guild(guild_id)
        if guild is None:
            return
        state = _get_guild_state(guild_id)
        await _do_publish_stats(guild, state)
        await _save_persisted_states()
        logger.info("Stats image published guild=%s", guild_id)
    except asyncio.CancelledError:
        raise
    finally:
        current = asyncio.current_task()
        if current is not None and _publish_tasks.get(guild_id) is current:
            _publish_tasks.pop(guild_id, None)


def _schedule_publish_stats(guild_id: int) -> None:
    existing = _publish_tasks.get(guild_id)
    if existing is not None and not existing.done():
        existing.cancel()
    _publish_tasks[guild_id] = asyncio.create_task(_debounced_publish_worker(guild_id))


async def _publish_stats(
    guild: discord.Guild,
    state: GuildStatsState,
    *,
    force: bool = False,
) -> None:
    if force:
        pending = _publish_tasks.get(guild.id)
        if pending is not None and not pending.done():
            pending.cancel()
            try:
                await pending
            except asyncio.CancelledError:
                pass
        await _do_publish_stats(guild, state)
        return
    _schedule_publish_stats(guild.id)


async def _handle_killfeed_message(message: discord.Message) -> None:
    if message.guild is None:
        return

    channels = channel_config.get_channels(message.guild.id)
    if channels is None or message.channel.id != channels["source_channel_id"]:
        return

    async with _guild_lock(message.guild.id):
        await _apply_killfeed_message(message, message.guild.id, update_stats=True)


@bot.event
async def on_ready() -> None:
    logger.info("Logged in as %s", bot.user)
    for guild in bot.guilds:
        added = await _catchup_new_killfeed(guild)
        if added:
            logger.info("Startup catch-up guild=%s kills=%s", guild.id, added)


@bot.event
async def setup_hook() -> None:
    _load_persisted_states()

    if not _killfeed_watch_loop.is_running():
        _killfeed_watch_loop.start()

    if SYNC_GUILD_ID and SYNC_GUILD_ID.isdigit():
        guild = discord.Object(id=int(SYNC_GUILD_ID))
        bot.tree.copy_global_to(guild=guild)
        synced = await bot.tree.sync(guild=guild)
        logger.info("Slash commands synced to guild %s (%s)", guild.id, len(synced))
        return

    if SYNC_GUILD_ID:
        logger.warning("SYNC_GUILD_ID invalid: %s", SYNC_GUILD_ID)

    synced = await bot.tree.sync()
    logger.info("Global slash commands synced (%s)", len(synced))


@bot.event
async def on_message(message: discord.Message) -> None:
    if bot.user and message.author.id == bot.user.id:
        return
    await _handle_killfeed_message(message)
    await bot.process_commands(message)


@tasks.loop(seconds=KILLFEED_POLL_INTERVAL_SEC)
async def _killfeed_watch_loop() -> None:
    """Подстраховка: новые kill-сообщения, если on_message не сработал."""
    for guild_id_str in channel_config.load_channel_config():
        try:
            guild_id = int(guild_id_str)
        except ValueError:
            continue
        guild = bot.get_guild(guild_id)
        if guild is None:
            continue
        await _catchup_new_killfeed(guild)


@_killfeed_watch_loop.before_loop
async def _before_killfeed_watch() -> None:
    await bot.wait_until_ready()


@bot.tree.command(
    name="set_stat_channels",
    description="Канал killfeed (источник) и канал для таблицы статистики кланов",
)
@app_commands.checks.has_permissions(manage_guild=True)
async def set_stat_channels(
    interaction: discord.Interaction,
    killfeed_channel: discord.TextChannel,
    stats_channel: discord.TextChannel,
) -> None:
    if interaction.guild is None:
        await interaction.response.send_message("Только на сервере.", ephemeral=True)
        return

    channel_config.set_channels(interaction.guild.id, killfeed_channel.id, stats_channel.id)
    await interaction.response.send_message(
        f"Настроено: killfeed {killfeed_channel.mention} → статистика {stats_channel.mention}.\n"
        "Запустите `/rebuild_stats` для первичного разбора истории канала.",
        ephemeral=True,
    )


@bot.tree.command(name="unset_stat_channels", description="Отключить сбор статистики")
@app_commands.checks.has_permissions(manage_guild=True)
async def unset_stat_channels(interaction: discord.Interaction) -> None:
    if interaction.guild is None:
        await interaction.response.send_message("Только на сервере.", ephemeral=True)
        return

    removed = channel_config.remove_channels(interaction.guild.id)
    if removed:
        await interaction.response.send_message("Сбор статистики отключён.", ephemeral=True)
        return
    await interaction.response.send_message("Каналы не были настроены.", ephemeral=True)


@bot.tree.command(name="stat_channels_info", description="Текущие каналы killfeed и статистики")
@app_commands.checks.has_permissions(manage_guild=True)
async def stat_channels_info(interaction: discord.Interaction) -> None:
    if interaction.guild is None:
        await interaction.response.send_message("Только на сервере.", ephemeral=True)
        return

    channels = channel_config.get_channels(interaction.guild.id)
    if channels is None:
        await interaction.response.send_message("Каналы не настроены.", ephemeral=True)
        return

    source = interaction.guild.get_channel(channels["source_channel_id"])
    target = interaction.guild.get_channel(channels["stats_channel_id"])
    source_view = source.mention if isinstance(source, discord.TextChannel) else channels["source_channel_id"]
    target_view = target.mention if isinstance(target, discord.TextChannel) else channels["stats_channel_id"]
    count_from = channel_config.get_count_from_utc(interaction.guild.id)
    if count_from is None:
        period_line = f"Учёт с: все сообщения (часовой пояс по умолчанию: `{DEFAULT_TIMEZONE}`)"
    else:
        period_line = f"Учёт с: **{format_count_from_display(count_from)}**"
    await interaction.response.send_message(
        f"Killfeed: {source_view}\nСтатистика: {target_view}\n{period_line}",
        ephemeral=True,
    )


@bot.tree.command(
    name="rebuild_stats",
    description="Пересчитать статистику по всей истории канала killfeed",
)
@app_commands.checks.has_permissions(manage_guild=True)
async def rebuild_stats(interaction: discord.Interaction) -> None:
    if interaction.guild is None:
        await interaction.response.send_message("Только на сервере.", ephemeral=True)
        return

    channels = channel_config.get_channels(interaction.guild.id)
    if channels is None:
        await interaction.response.send_message("Сначала `/set_stat_channels`.", ephemeral=True)
        return

    source = interaction.guild.get_channel(channels["source_channel_id"])
    if not isinstance(source, discord.TextChannel):
        await interaction.response.send_message("Канал killfeed не найден.", ephemeral=True)
        return

    await interaction.response.defer(ephemeral=True)
    state = _get_guild_state(interaction.guild.id)
    async with _guild_lock(interaction.guild.id):
        count = await _process_channel_history(source, state, interaction.guild.id, full_rebuild=True)
        await _save_persisted_states()
        await _publish_stats(interaction.guild, state, force=True)
    period = _period_note(interaction.guild.id)
    period_suffix = f"\n{period}" if period else ""
    await interaction.followup.send(
        f"Готово: учтено **{count}** kill-сообщений.{period_suffix}\n"
        "Таблица опубликована. Каждый новый kill в канале killfeed сразу обновляет её.",
        ephemeral=True,
    )


@bot.tree.command(
    name="set_stat_count_from",
    description="Учитывать killfeed только с указанной даты и времени",
)
@app_commands.describe(
    date="Дата: ДД.ММ.ГГГГ (01.06.2025) или ГГГГ-ММ-ДД",
    time="Время ЧЧ:ММ (по умолчанию 00:00, зона STATS_TIMEZONE / Europe/Moscow)",
)
@app_commands.checks.has_permissions(manage_guild=True)
async def set_stat_count_from(
    interaction: discord.Interaction,
    date: str,
    time: str = "00:00",
) -> None:
    if interaction.guild is None:
        await interaction.response.send_message("Только на сервере.", ephemeral=True)
        return

    try:
        when_utc = parse_count_from(date, time)
    except ValueError as exc:
        await interaction.response.send_message(str(exc), ephemeral=True)
        return

    channel_config.set_count_from_utc(interaction.guild.id, when_utc)
    await interaction.response.send_message(
        f"Учёт killfeed с **{format_count_from_display(when_utc)}**.\n"
        "Запустите `/rebuild_stats`, чтобы пересчитать статистику.",
        ephemeral=True,
    )


@bot.tree.command(
    name="clear_stat_count_from",
    description="Учитывать все сообщения killfeed (без ограничения по дате)",
)
@app_commands.checks.has_permissions(manage_guild=True)
async def clear_stat_count_from(interaction: discord.Interaction) -> None:
    if interaction.guild is None:
        await interaction.response.send_message("Только на сервере.", ephemeral=True)
        return

    removed = channel_config.clear_count_from_utc(interaction.guild.id)
    if not removed:
        await interaction.response.send_message("Ограничение по дате не было задано.", ephemeral=True)
        return

    await interaction.response.send_message(
        "Ограничение по дате снято. Запустите `/rebuild_stats` для пересчёта по всей истории.",
        ephemeral=True,
    )


@bot.tree.command(name="refresh_stats", description="Обновить сообщение со статистикой без пересчёта")
@app_commands.checks.has_permissions(manage_guild=True)
async def refresh_stats(interaction: discord.Interaction) -> None:
    if interaction.guild is None:
        await interaction.response.send_message("Только на сервере.", ephemeral=True)
        return

    await interaction.response.defer(ephemeral=True)
    state = _get_guild_state(interaction.guild.id)
    await _publish_stats(interaction.guild, state, force=True)
    await _save_persisted_states()
    await interaction.followup.send("Сообщение со статистикой обновлено.", ephemeral=True)


@set_stat_channels.error
@unset_stat_channels.error
@stat_channels_info.error
@set_stat_count_from.error
@clear_stat_count_from.error
@rebuild_stats.error
@refresh_stats.error
async def admin_error(interaction: discord.Interaction, error: app_commands.AppCommandError) -> None:
    if isinstance(error, app_commands.MissingPermissions):
        await _safe_interaction_reply(interaction, "Нужно право «Управление сервером».")
        return
    logger.exception("Command error: %s", error)
    await _safe_interaction_reply(interaction, "Ошибка выполнения команды.")


if __name__ == "__main__":
    bot.run(TOKEN)
