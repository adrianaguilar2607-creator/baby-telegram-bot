import json
import logging
import os
from copy import deepcopy
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from zoneinfo import ZoneInfo

from telegram import ReplyKeyboardMarkup, Update
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

# =========================
# Configuración
# =========================
BOT_TOKEN = os.getenv("BOT_TOKEN")
TIMEZONE = ZoneInfo("Europe/Madrid")
DATA_FILE = Path("baby_bot_data.json")

FEED_INTERVAL = timedelta(hours=4)
NAP_INTERVAL = timedelta(hours=2, minutes=30)
REMINDER_BEFORE = timedelta(minutes=15)

TUMMY_TIME_HOUR = 9
TUMMY_TIME_MINUTE = 30

CALM_DOWN_HOUR = 18
CALM_DOWN_MINUTE = 0

# Botones
BUTTON_FEED_NOW = "🍼 Comida ahora"
BUTTON_FEED_TIME = "🍼 Comida con hora"
BUTTON_NAP_START_NOW = "😴 Siesta inicia ahora"
BUTTON_NAP_END_NOW = "😴 Siesta termina ahora"
BUTTON_NIGHT_START_NOW = "🌙 Noche inicia ahora"
BUTTON_NIGHT_END_NOW = "🌙 Noche termina ahora"
BUTTON_STATUS = "📊 Ver estado"
BUTTON_HISTORY = "📅 Historial de hoy"

# =========================
# Logging
# =========================
logging.basicConfig(
    format="%(asctime)s | %(name)s | %(levelname)s | %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

# =========================
# Estado global
# =========================
STATE: Dict[str, Any] = {}


# =========================
# Utilidades base
# =========================
def now_local() -> datetime:
    return datetime.now(TIMEZONE)


def dt_to_str(dt: Optional[datetime]) -> Optional[str]:
    if dt is None:
        return None
    return dt.astimezone(TIMEZONE).isoformat()


def str_to_dt(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(value)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=TIMEZONE)
        return dt.astimezone(TIMEZONE)
    except Exception:
        return None


def fmt_time(dt: Optional[datetime]) -> str:
    return dt.strftime("%H:%M") if dt else "—"


def fmt_datetime(dt: Optional[datetime]) -> str:
    return dt.strftime("%d/%m %H:%M") if dt else "—"


def keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        [
            [BUTTON_FEED_NOW, BUTTON_FEED_TIME],
            [BUTTON_NAP_START_NOW, BUTTON_NAP_END_NOW],
            [BUTTON_NIGHT_START_NOW, BUTTON_NIGHT_END_NOW],
            [BUTTON_STATUS, BUTTON_HISTORY],
        ],
        resize_keyboard=True,
        one_time_keyboard=False,
    )


def parse_time_text(text: str) -> Optional[Tuple[int, int]]:
    try:
        parts = text.strip().split(":")
        if len(parts) != 2:
            return None
        hour = int(parts[0])
        minute = int(parts[1])
        if hour < 0 or hour > 23 or minute < 0 or minute > 59:
            return None
        return hour, minute
    except Exception:
        return None


def parse_manual_time(text: str) -> Optional[datetime]:
    parsed = parse_time_text(text)
    if not parsed:
        return None
    hour, minute = parsed
    now = now_local()
    return datetime(
        year=now.year,
        month=now.month,
        day=now.day,
        hour=hour,
        minute=minute,
        tzinfo=TIMEZONE,
    )


def format_remaining(target_dt: Optional[datetime]) -> str:
    if target_dt is None:
        return "—"

    diff = target_dt - now_local()
    total_seconds = int(diff.total_seconds())

    if total_seconds <= 0:
        minutes_late = abs(total_seconds) // 60
        if minutes_late == 0:
            return "ahora"
        return f"retrasado {minutes_late} min"

    hours = total_seconds // 3600
    minutes = (total_seconds % 3600) // 60

    if hours > 0:
        return f"{hours} h {minutes} min"
    return f"{minutes} min"


def format_duration(delta: timedelta) -> str:
    total_minutes = int(delta.total_seconds() // 60)
    if total_minutes < 0:
        total_minutes = 0
    hours = total_minutes // 60
    minutes = total_minutes % 60
    return f"{hours} h {minutes} min"


# =========================
# Persistencia
# =========================
def default_chat_state() -> Dict[str, Any]:
    return {
        "chat_id": None,
        "baby_name": "bebé",
        "last_feed": None,
        "last_day_nap_end": None,
        "active_day_nap_start": None,
        "active_night_sleep_start": None,
        "history": [],
        "reminders": {
            "feed_15_sent": False,
            "feed_due_sent": False,
            "nap_15_sent": False,
            "nap_due_sent": False,
        },
        "daily_messages": {
            "date": None,
            "tummy_time_sent": False,
            "calm_down_sent": False,
        },
    }


def load_data() -> Dict[str, Any]:
    if not DATA_FILE.exists():
        return {"chats": {}}

    try:
        with DATA_FILE.open("r", encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, dict):
            return {"chats": {}}
        if "chats" not in data or not isinstance(data["chats"], dict):
            data["chats"] = {}
        return data
    except Exception as e:
        logger.warning("No se pudo leer JSON, se usará estado vacío: %s", e)
        return {"chats": {}}


def save_data() -> None:
    temp_file = DATA_FILE.with_suffix(".tmp")
    temp_file.write_text(
        json.dumps(STATE, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    temp_file.replace(DATA_FILE)


def get_chat_state(chat_id: int) -> Dict[str, Any]:
    chat_key = str(chat_id)
    chats = STATE.setdefault("chats", {})
    if chat_key not in chats or not isinstance(chats[chat_key], dict):
        chats[chat_key] = default_chat_state()

    current = chats[chat_key]
    base = default_chat_state()

    for key, value in base.items():
        if key not in current:
            current[key] = deepcopy(value)

    current["chat_id"] = chat_id
    return current


def cleanup_old_history(chat_data: Dict[str, Any]) -> None:
    history = chat_data.get("history", [])
    if not isinstance(history, list):
        chat_data["history"] = []
        return

    cutoff = now_local() - timedelta(days=14)
    cleaned = []
    for item in history:
        if not isinstance(item, dict):
            continue
        dt = str_to_dt(item.get("time"))
        if dt and dt >= cutoff:
            cleaned.append(item)

    chat_data["history"] = cleaned


def add_history_event(chat_data: Dict[str, Any], event_type: str, dt: datetime, extra: Optional[Dict[str, Any]] = None) -> None:
    cleanup_old_history(chat_data)
    item = {
        "type": event_type,
        "time": dt_to_str(dt),
    }
    if extra:
        item.update(extra)
    history = chat_data.setdefault("history", [])
    history.append(item)
    history.sort(key=lambda x: x.get("time", ""))


# =========================
# Cálculos de siesta / noche
# =========================
def get_today_events(chat_data: Dict[str, Any]) -> List[Dict[str, Any]]:
    today = now_local().date()
    result = []

    for item in chat_data.get("history", []):
        dt = str_to_dt(item.get("time"))
        if dt and dt.date() == today:
            row = dict(item)
            row["dt"] = dt
            result.append(row)

    result.sort(key=lambda x: x["dt"])
    return result


def completed_day_naps_today(chat_data: Dict[str, Any]) -> List[Dict[str, Any]]:
    today = now_local().date()
    result = []

    for item in chat_data.get("history", []):
        if item.get("type") != "day_nap_end":
            continue

        end_dt = str_to_dt(item.get("time"))
        start_dt = str_to_dt(item.get("start_time"))
        if not end_dt or not start_dt:
            continue

        if end_dt.date() != today and start_dt.date() != today:
            continue

        result.append(
            {
                "start": start_dt,
                "end": end_dt,
                "duration": end_dt - start_dt,
            }
        )

    result.sort(key=lambda x: x["start"])
    return result


def total_day_nap_today(chat_data: Dict[str, Any]) -> timedelta:
    total = timedelta()
    for nap in completed_day_naps_today(chat_data):
        if nap["duration"].total_seconds() > 0:
            total += nap["duration"]

    active_start = str_to_dt(chat_data.get("active_day_nap_start"))
    if active_start and active_start.date() == now_local().date():
        total += now_local() - active_start

    return total


def last_completed_day_nap(chat_data: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    naps = completed_day_naps_today(chat_data)
    if not naps:
        return None
    return naps[-1]


def next_feed_time(chat_data: Dict[str, Any]) -> Optional[datetime]:
    last_feed = str_to_dt(chat_data.get("last_feed"))
    if not last_feed:
        return None
    return last_feed + FEED_INTERVAL


def next_day_nap_time(chat_data: Dict[str, Any]) -> Optional[datetime]:
    active_start = str_to_dt(chat_data.get("active_day_nap_start"))
    if active_start:
        return None

    last_day_nap_end = str_to_dt(chat_data.get("last_day_nap_end"))
    if not last_day_nap_end:
        return None
    return last_day_nap_end + NAP_INTERVAL


def reset_feed_reminders(chat_data: Dict[str, Any]) -> None:
    chat_data["reminders"]["feed_15_sent"] = False
    chat_data["reminders"]["feed_due_sent"] = False


def reset_nap_reminders(chat_data: Dict[str, Any]) -> None:
    chat_data["reminders"]["nap_15_sent"] = False
    chat_data["reminders"]["nap_due_sent"] = False


def baby_name(chat_data: Dict[str, Any]) -> str:
    name = (chat_data.get("baby_name") or "").strip()
    return name if name else "bebé"


# =========================
# Registro de eventos
# =========================
def register_feed(chat_data: Dict[str, Any], event_dt: datetime) -> None:
    chat_data["last_feed"] = dt_to_str(event_dt)
    add_history_event(chat_data, "feed", event_dt)
    reset_feed_reminders(chat_data)
    save_data()


def start_day_nap(chat_data: Dict[str, Any], start_dt: datetime) -> Tuple[bool, str]:
    if str_to_dt(chat_data.get("active_day_nap_start")):
        return False, "Ya hay una siesta en curso."

    if str_to_dt(chat_data.get("active_night_sleep_start")):
        return False, "Primero termina el sueño nocturno."

    chat_data["active_day_nap_start"] = dt_to_str(start_dt)
    add_history_event(chat_data, "day_nap_start", start_dt)
    save_data()
    return True, f"😴 Siesta iniciada a las {fmt_time(start_dt)}"


def end_day_nap(chat_data: Dict[str, Any], end_dt: datetime) -> Tuple[bool, str]:
    start_dt = str_to_dt(chat_data.get("active_day_nap_start"))
    if not start_dt:
        return False, "No hay una siesta en curso."

    if end_dt < start_dt:
        return False, "La hora final no puede ser antes del inicio."

    duration = end_dt - start_dt
    chat_data["active_day_nap_start"] = None
    chat_data["last_day_nap_end"] = dt_to_str(end_dt)

    add_history_event(
        chat_data,
        "day_nap_end",
        end_dt,
        extra={
            "start_time": dt_to_str(start_dt),
            "duration_minutes": int(duration.total_seconds() // 60),
        },
    )

    reset_nap_reminders(chat_data)
    save_data()
    return True, f"😴 Siesta terminada a las {fmt_time(end_dt)} ({format_duration(duration)})"


def start_night_sleep(chat_data: Dict[str, Any], start_dt: datetime) -> Tuple[bool, str]:
    if str_to_dt(chat_data.get("active_night_sleep_start")):
        return False, "Ya hay sueño nocturno en curso."

    if str_to_dt(chat_data.get("active_day_nap_start")):
        return False, "Primero termina la siesta actual."

    chat_data["active_night_sleep_start"] = dt_to_str(start_dt)
    add_history_event(chat_data, "night_sleep_start", start_dt)
    save_data()
    return True, f"🌙 Sueño nocturno iniciado a las {fmt_time(start_dt)}"


def end_night_sleep(chat_data: Dict[str, Any], end_dt: datetime) -> Tuple[bool, str]:
    start_dt = str_to_dt(chat_data.get("active_night_sleep_start"))
    if not start_dt:
        return False, "No hay sueño nocturno en curso."

    if end_dt < start_dt:
        return False, "La hora final no puede ser antes del inicio."

    duration = end_dt - start_dt
    chat_data["active_night_sleep_start"] = None

    add_history_event(
        chat_data,
        "night_sleep_end",
        end_dt,
        extra={
            "start_time": dt_to_str(start_dt),
            "duration_minutes": int(duration.total_seconds() // 60),
        },
    )

    save_data()
    return True, f"🌙 Sueño nocturno terminado a las {fmt_time(end_dt)} ({format_duration(duration)})"


# =========================
# Textos
# =========================
def build_status_text(chat_data: Dict[str, Any]) -> str:
    last_feed = str_to_dt(chat_data.get("last_feed"))
    next_feed = next_feed_time(chat_data)

    active_day_nap = str_to_dt(chat_data.get("active_day_nap_start"))
    active_night_sleep = str_to_dt(chat_data.get("active_night_sleep_start"))
    next_nap = next_day_nap_time(chat_data)
    total_naps = total_day_nap_today(chat_data)

    last_nap = last_completed_day_nap(chat_data)
    last_nap_text = "—"
    if last_nap:
        last_nap_text = f"{fmt_time(last_nap['start'])} - {fmt_time(last_nap['end'])} ({format_duration(last_nap['duration'])})"

    if active_day_nap:
        nap_state = f"En curso desde {fmt_time(active_day_nap)}"
    elif next_nap:
        nap_state = f"Próxima siesta: {fmt_datetime(next_nap)}"
    else:
        nap_state = "Próxima siesta: —"

    if active_night_sleep:
        night_state = f"🌙 Sueño nocturno en curso desde {fmt_time(active_night_sleep)}"
    else:
        night_state = "🌙 Sueño nocturno: no activo"

    lines = [
        f"👶 Estado de {baby_name(chat_data)}",
        "",
        f"🍼 Última comida: {fmt_datetime(last_feed)}",
        f"🍼 Próxima comida: {fmt_datetime(next_feed)}",
        f"⏳ Falta comida: {format_remaining(next_feed)}",
        "",
        f"😴 Última siesta completa: {last_nap_text}",
        f"😴 {nap_state}",
        f"⏳ Falta siesta: {'en curso' if active_day_nap else format_remaining(next_nap)}",
        f"🕒 Total siestas de hoy: {format_duration(total_naps)}",
        "",
        night_state,
    ]
    return "\n".join(lines)


def build_today_history_text(chat_data: Dict[str, Any]) -> str:
    events = get_today_events(chat_data)

    if not events and not str_to_dt(chat_data.get("active_day_nap_start")) and not str_to_dt(chat_data.get("active_night_sleep_start")):
        return "📅 Hoy no hay registros todavía."

    feeds = []
    day_naps = []
    night_sleep = []

    for item in events:
        event_type = item.get("type")
        dt = item["dt"]

        if event_type == "feed":
            feeds.append(f"🍼 {dt.strftime('%H:%M')}")

        elif event_type == "day_nap_end":
            start_dt = str_to_dt(item.get("start_time"))
            if start_dt:
                duration = dt - start_dt
                day_naps.append(
                    f"😴 {start_dt.strftime('%H:%M')} - {dt.strftime('%H:%M')} ({format_duration(duration)})"
                )

        elif event_type == "night_sleep_end":
            start_dt = str_to_dt(item.get("start_time"))
            if start_dt:
                duration = dt - start_dt
                night_sleep.append(
                    f"🌙 {start_dt.strftime('%d/%m %H:%M')} - {dt.strftime('%d/%m %H:%M')} ({format_duration(duration)})"
                )

    active_day_nap = str_to_dt(chat_data.get("active_day_nap_start"))
    if active_day_nap and active_day_nap.date() == now_local().date():
        day_naps.append(f"😴 {active_day_nap.strftime('%H:%M')} - en curso")

    active_night_sleep = str_to_dt(chat_data.get("active_night_sleep_start"))
    if active_night_sleep:
        night_sleep.append(f"🌙 {active_night_sleep.strftime('%d/%m %H:%M')} - en curso")

    lines = ["📅 Historial de hoy", ""]

    lines.append("🍼 Comidas:")
    if feeds:
        lines.extend(feeds)
    else:
        lines.append("—")

    lines.append("")
    lines.append(f"😴 Siestas del día ({format_duration(total_day_nap_today(chat_data))}):")
    if day_naps:
        lines.extend(day_naps)
    else:
        lines.append("—")

    lines.append("")
    lines.append("🌙 Sueño nocturno:")
    if night_sleep:
        lines.extend(night_sleep)
    else:
        lines.append("—")

    return "\n".join(lines)


# =========================
# Envíos
# =========================
async def send_with_keyboard(update: Update, text: str) -> None:
    if update.message:
        await update.message.reply_text(text, reply_markup=keyboard())


async def send_to_chat(context: ContextTypes.DEFAULT_TYPE, chat_id: int, text: str) -> None:
    await context.bot.send_message(chat_id=chat_id, text=text, reply_markup=keyboard())


# =========================
# Comandos
# =========================
async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.effective_chat:
        return

    chat_id = update.effective_chat.id
    chat_data = get_chat_state(chat_id)
    save_data()

    text = (
        f"Hola. Ya guardé este chat para los recordatorios de {baby_name(chat_data)}.\n\n"
        "Usa /help para ver comandos."
    )
    await send_with_keyboard(update, text)


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    text = (
        "Comandos:\n"
        "/start\n"
        "/help\n"
        "/setname Nombre\n"
        "/feed\n"
        "/feed HH:MM\n"
        "/napstart\n"
        "/napstart HH:MM\n"
        "/napend\n"
        "/napend HH:MM\n"
        "/nightstart\n"
        "/nightstart HH:MM\n"
        "/nightend\n"
        "/nightend HH:MM\n"
        "/status\n"
        "/history"
    )
    await send_with_keyboard(update, text)


async def setname_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.effective_chat:
        return

    chat_data = get_chat_state(update.effective_chat.id)

    if not context.args:
        await send_with_keyboard(update, "Usa /setname Nombre")
        return

    name = " ".join(context.args).strip()
    if not name:
        await send_with_keyboard(update, "Usa /setname Nombre")
        return

    chat_data["baby_name"] = name
    save_data()
    await send_with_keyboard(update, f"Nombre guardado: {name}")


async def feed_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.effective_chat:
        return

    chat_data = get_chat_state(update.effective_chat.id)

    if context.args:
        dt = parse_manual_time(context.args[0])
        if dt is None:
            await send_with_keyboard(update, "Usa /feed HH:MM")
            return
        register_feed(chat_data, dt)
        await send_with_keyboard(update, f"🍼 Comida registrada a las {fmt_time(dt)}")
        return

    dt = now_local()
    register_feed(chat_data, dt)
    await send_with_keyboard(update, f"🍼 Comida registrada a las {fmt_time(dt)}")


async def napstart_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.effective_chat:
        return

    chat_data = get_chat_state(update.effective_chat.id)

    if context.args:
        dt = parse_manual_time(context.args[0])
        if dt is None:
            await send_with_keyboard(update, "Usa /napstart HH:MM")
            return
        ok, message = start_day_nap(chat_data, dt)
        await send_with_keyboard(update, message)
        return

    dt = now_local()
    ok, message = start_day_nap(chat_data, dt)
    await send_with_keyboard(update, message)


async def napend_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.effective_chat:
        return

    chat_data = get_chat_state(update.effective_chat.id)

    if context.args:
        dt = parse_manual_time(context.args[0])
        if dt is None:
            await send_with_keyboard(update, "Usa /napend HH:MM")
            return
        ok, message = end_day_nap(chat_data, dt)
        await send_with_keyboard(update, message)
        return

    dt = now_local()
    ok, message = end_day_nap(chat_data, dt)
    await send_with_keyboard(update, message)


async def nightstart_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.effective_chat:
        return

    chat_data = get_chat_state(update.effective_chat.id)

    if context.args:
        dt = parse_manual_time(context.args[0])
        if dt is None:
            await send_with_keyboard(update, "Usa /nightstart HH:MM")
            return
        ok, message = start_night_sleep(chat_data, dt)
        await send_with_keyboard(update, message)
        return

    dt = now_local()
    ok, message = start_night_sleep(chat_data, dt)
    await send_with_keyboard(update, message)


async def nightend_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.effective_chat:
        return

    chat_data = get_chat_state(update.effective_chat.id)

    if context.args:
        dt = parse_manual_time(context.args[0])
        if dt is None:
            await send_with_keyboard(update, "Usa /nightend HH:MM")
            return
        ok, message = end_night_sleep(chat_data, dt)
        await send_with_keyboard(update, message)
        return

    dt = now_local()
    ok, message = end_night_sleep(chat_data, dt)
    await send_with_keyboard(update, message)


async def status_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.effective_chat:
        return
    chat_data = get_chat_state(update.effective_chat.id)
    await send_with_keyboard(update, build_status_text(chat_data))


async def history_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.effective_chat:
        return
    chat_data = get_chat_state(update.effective_chat.id)
    await send_with_keyboard(update, build_today_history_text(chat_data))


# =========================
# Botones
# =========================
async def button_feed_now(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.effective_chat:
        return
    chat_data = get_chat_state(update.effective_chat.id)
    dt = now_local()
    register_feed(chat_data, dt)
    await send_with_keyboard(update, f"🍼 Comida registrada a las {fmt_time(dt)}")


async def button_feed_time(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await send_with_keyboard(update, "Usa /feed HH:MM")


async def button_nap_start_now(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.effective_chat:
        return
    chat_data = get_chat_state(update.effective_chat.id)
    dt = now_local()
    ok, message = start_day_nap(chat_data, dt)
    await send_with_keyboard(update, message)


async def button_nap_end_now(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.effective_chat:
        return
    chat_data = get_chat_state(update.effective_chat.id)
    dt = now_local()
    ok, message = end_day_nap(chat_data, dt)
    await send_with_keyboard(update, message)


async def button_night_start_now(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.effective_chat:
        return
    chat_data = get_chat_state(update.effective_chat.id)
    dt = now_local()
    ok, message = start_night_sleep(chat_data, dt)
    await send_with_keyboard(update, message)


async def button_night_end_now(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.effective_chat:
        return
    chat_data = get_chat_state(update.effective_chat.id)
    dt = now_local()
    ok, message = end_night_sleep(chat_data, dt)
    await send_with_keyboard(update, message)


async def button_status(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await status_command(update, context)


async def button_history(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await history_command(update, context)


async def unknown_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await send_with_keyboard(update, "Usa /help")


# =========================
# Recordatorios automáticos
# =========================
async def periodic_checks(context: ContextTypes.DEFAULT_TYPE) -> None:
    chats = STATE.get("chats", {})
    if not isinstance(chats, dict):
        return

    current_now = now_local()
    current_date_str = current_now.date().isoformat()
    changed = False

    for chat_key, chat_data in chats.items():
        try:
            chat_id = int(chat_key)
        except Exception:
            continue

        if not isinstance(chat_data, dict):
            continue

        chat_data.setdefault("reminders", {})
        chat_data.setdefault("daily_messages", {})
        cleanup_old_history(chat_data)

        reminders = chat_data["reminders"]
        daily = chat_data["daily_messages"]

        next_feed = next_feed_time(chat_data)
        next_nap = next_day_nap_time(chat_data)

        # Comida
        if next_feed:
            feed_15_time = next_feed - REMINDER_BEFORE

            if current_now >= feed_15_time and current_now < next_feed:
                if not reminders.get("feed_15_sent", False):
                    await send_to_chat(
                        context,
                        chat_id,
                        f"🍼 En 15 min toca comida para {baby_name(chat_data)}.",
                    )
                    reminders["feed_15_sent"] = True
                    changed = True

            if current_now >= next_feed:
                if not reminders.get("feed_due_sent", False):
                    await send_to_chat(
                        context,
                        chat_id,
                        f"🍼 Ya toca comida para {baby_name(chat_data)}.",
                    )
                    reminders["feed_due_sent"] = True
                    changed = True

        # Siesta del día
        if next_nap:
            nap_15_time = next_nap - REMINDER_BEFORE

            if current_now >= nap_15_time and current_now < next_nap:
                if not reminders.get("nap_15_sent", False):
                    await send_to_chat(
                        context,
                        chat_id,
                        f"😴 En 15 min toca siesta para {baby_name(chat_data)}.",
                    )
                    reminders["nap_15_sent"] = True
                    changed = True

            if current_now >= next_nap:
                if not reminders.get("nap_due_sent", False):
                    await send_to_chat(
                        context,
                        chat_id,
                        f"😴 Ya toca siesta para {baby_name(chat_data)}.",
                    )
                    reminders["nap_due_sent"] = True
                    changed = True

        # Reinicio diario
        if daily.get("date") != current_date_str:
            daily["date"] = current_date_str
            daily["tummy_time_sent"] = False
            daily["calm_down_sent"] = False
            changed = True

        # Tummy time
        tummy_dt = datetime(
            current_now.year,
            current_now.month,
            current_now.day,
            TUMMY_TIME_HOUR,
            TUMMY_TIME_MINUTE,
            tzinfo=TIMEZONE,
        )
        if current_now >= tummy_dt and not daily.get("tummy_time_sent", False):
            await send_to_chat(
                context,
                chat_id,
                f"🤸 Tummy time de la mañana para {baby_name(chat_data)}.",
            )
            daily["tummy_time_sent"] = True
            changed = True

        # Bajar actividad
        calm_dt = datetime(
            current_now.year,
            current_now.month,
            current_now.day,
            CALM_DOWN_HOUR,
            CALM_DOWN_MINUTE,
            tzinfo=TIMEZONE,
        )
        if current_now >= calm_dt and not daily.get("calm_down_sent", False):
            await send_to_chat(
                context,
                chat_id,
                f"🌙 Hora de bajar actividad por la tarde para {baby_name(chat_data)}.",
            )
            daily["calm_down_sent"] = True
            changed = True

    if changed:
        save_data()


async def on_startup(app: Application) -> None:
    if app.job_queue is not None:
        app.job_queue.run_repeating(periodic_checks, interval=60, first=5)


# =========================
# Main
# =========================
def main() -> None:
    global STATE

    if not BOT_TOKEN:
        raise RuntimeError("Falta BOT_TOKEN en variables de entorno.")

    STATE = load_data()
    save_data()

    application = (
        Application.builder()
        .token(BOT_TOKEN)
        .post_init(on_startup)
        .build()
    )

    application.add_handler(CommandHandler("start", start_command))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(CommandHandler("setname", setname_command))
    application.add_handler(CommandHandler("feed", feed_command))
    application.add_handler(CommandHandler("napstart", napstart_command))
    application.add_handler(CommandHandler("napend", napend_command))
    application.add_handler(CommandHandler("nightstart", nightstart_command))
    application.add_handler(CommandHandler("nightend", nightend_command))
    application.add_handler(CommandHandler("status", status_command))
    application.add_handler(CommandHandler("history", history_command))

    application.add_handler(MessageHandler(filters.Regex(f"^{BUTTON_FEED_NOW}$"), button_feed_now))
    application.add_handler(MessageHandler(filters.Regex(f"^{BUTTON_FEED_TIME}$"), button_feed_time))
    application.add_handler(MessageHandler(filters.Regex(f"^{BUTTON_NAP_START_NOW}$"), button_nap_start_now))
    application.add_handler(MessageHandler(filters.Regex(f"^{BUTTON_NAP_END_NOW}$"), button_nap_end_now))
    application.add_handler(MessageHandler(filters.Regex(f"^{BUTTON_NIGHT_START_NOW}$"), button_night_start_now))
    application.add_handler(MessageHandler(filters.Regex(f"^{BUTTON_NIGHT_END_NOW}$"), button_night_end_now))
    application.add_handler(MessageHandler(filters.Regex(f"^{BUTTON_STATUS}$"), button_status))
    application.add_handler(MessageHandler(filters.Regex(f"^{BUTTON_HISTORY}$"), button_history))

    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, unknown_text))

    application.run_polling(drop_pending_updates=False)


if __name__ == "__main__":
    main()
