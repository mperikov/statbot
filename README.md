# statbot

Discord-бот для статистики кланов по каналу killfeed.

## Формат сообщений

Ожидается текст (как в выводе dsbot):

```text
[killer](<steam_url>) killed [victim](<steam_url>) with M4A1 from 412 meters
```

## Кланы

Никнейм делится на **слова** по пробелам, точкам и `^`; каждое слово — отдельный клан. Литерал **`...`** (три точки подряд) тоже считается отдельным кланом.  
Пример: `gfg . nick` → `gfg`, `nick`; `Clan^Player Name` → `Clan`, `Player`, `Name`.

При килле все слова из ника убийцы получают +1 убийство (и учёт дистанции), при смерти — все слова из ника жертвы получают +1 смерть. Повтор одного слова в нике считается один раз.

## Игроки

Учёт по **Steam ID** из ссылок в killfeed (`/profiles/7656…` или `/id/vanity`). В таблице показывается **последний ник** игрока. Отдельное сообщение в канале статистики — топ **7** игроков по убийствам.

## Вывод

Два **JPEG** на `images/statfon.jpg`: таблица кланов и таблица игроков (топ **7**, ~**74%** кадра). Без текстовой подписи.

Шрифт **Open Sans** лежит в `assets/fonts/` (в репозитории). На Linux без bundled-шрифта используются DejaVu/Liberation, если установлены. Переопределение: `STAT_FONT_PATH` в `.env`.

```env
STAT_BACKGROUND_PATH=C:\bots\statbot\images\statfon.jpg
```

## Метрики (топ 7 по убийствам)

- убийства  
- смерти  
- K/D  
- максимальная дистанция выстрела (по киллам этого клана)

## Установка

```bash
cd C:\bots\statbot
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
copy .env.example .env
# заполнить BOT_TOKEN в .env
python bot.py
```

В [Discord Developer Portal](https://discord.com/developers/applications) включите **Message Content Intent**.

## Команды

| Команда | Описание |
|---------|----------|
| `/set_stat_channels` | Канал killfeed и канал для таблицы |
| `/rebuild_stats` | Полный пересчёт по истории killfeed |
| `/refresh_stats` | Переотправить/обновить таблицу |
| `/set_stat_count_from` | Учёт только с даты/времени (зона `STATS_TIMEZONE`) |
| `/clear_stat_count_from` | Снять ограничение по дате |
| `/stat_channels_info` | Текущие каналы и дата начала учёта |
| `/unset_stat_channels` | Отключить |

После настройки каналов выполните `/rebuild_stats` один раз, чтобы учесть старые сообщения.  
После пересчёта новые kill обновляют таблицу с задержкой **5 с** после последнего kill (`STATS_PUBLISH_DEBOUNCE_SEC`) — так Discord не режет по rate limit.

Ограничение по дате: `/set_stat_count_from date:01.06.2025 time:14:30` — в `.env` можно задать `STATS_TIMEZONE=Europe/Moscow` (по умолчанию Москва). После смены даты снова нужен `/rebuild_stats`.
