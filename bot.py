import json
import logging
import os
from copy import deepcopy
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from zoneinfo import ZoneInfo

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardMarkup, Update
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

# =========================
# Configuracion
# =========================
BOT_TOKEN = os.getenv("BOT_TOKEN")
TIMEZONE = ZoneInfo("Europe/Madrid")
DATA_FILE = Path("baby_bot_data.json")
REMINDER_BEFORE = timedelta(minutes=15)

# Horario base de Sofia (hora_inicio, min_inicio, hora_fin, min_fin, tipo, etiqueta)
SOFIA_SCHEDULE = [
    (5,  30, 6,  0,  "biberon",  "Biberón mañana (180ml)"),
    (8,  30, 9,  0,  "nap",      "Siesta 1"),
    (9,  30, 10, 0,  "biberon",  "Biberón media mañana (180ml)"),
    (11, 30, 12, 0,  "solido",   "Sólido almuerzo (puré)"),
    (12, 30, 13, 0,  "nap",      "Siesta 2"),
    (14, 0,  14, 30, "biberon",  "Biberón mediodía (210ml)"),
    (15, 30, 16, 0,  "nap",      "Siesta 3"),
    (16, 30, 17, 0,  "solido",   "Sólido merienda"),
    (17, 30, 18, 0,  "biberon",  "Biberón tarde (180ml)"),
    (20, 0,  20, 0,  "night",    "Dormir noche"),
    (22, 30, 23, 0,  "biberon",  "Toma nocturna (180ml)"),
]

# Vigilia maxima antes de dormir (en minutos)
MAX_AWAKE_BEFORE_BEDTIME = 150  # 2.5 horas

# Rangos de sueno por edad en meses (siestas_min_h, siestas_max_h, noche_min_h, noche_max_h)
SLEEP_RANGES = {
    4:  (3.0, 5.0, 10.0, 12.0),
    5:  (3.0, 4.5, 10.0, 12.0),
    6:  (2.5, 4.0, 10.0, 12.0),
    7:  (2.0, 3.5, 10.0, 12.0),
    8:  (2.0, 3.0, 10.0, 12.0),
    9:  (1.5, 3.0, 10.0, 12.0),
    10: (1.5, 2.5, 10.0, 12.0),
    11: (1.0, 2.0, 10.0, 12.0),
    12: (1.0, 2.0, 10.0, 12.0),
}

SOFIA_BIRTHDATE = "2024-09-15"  # Actualiza con la fecha real de nacimiento

# Botones principales
BUTTON_NAP      = "😴 Siesta"
BUTTON_NIGHT    = "🌙 Noche"
BUTTON_FEED     = "🍼 Alimentación"
BUTTON_STATUS   = "📊 Estado"
BUTTON_HISTORY  = "📅 Historial"
BUTTON_WEEKLY   = "📈 Resumen semanal"
BUTTON_FOODS    = "🍎 Alimentos"
BUTTON_MENU     = "🗓️ Menú"
BUTTON_SLEEP_REC = "💤 Rec. sueño"
BUTTON_SCHEDULE = "🕐 Horario"
BUTTON_TRANSITION = "🥄 Transición sólidos"
BUTTON_UNDO     = "❌ Anular"

# =========================
# Logging
# =========================
logging.basicConfig(
    format="%(asctime)s | %(name)s | %(levelname)s | %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

STATE: Dict[str, Any] = {}


# =========================
# Utilidades
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


def format_duration(delta: timedelta) -> str:
    total_minutes = int(delta.total_seconds() // 60)
    if total_minutes < 0:
        total_minutes = 0
    h = total_minutes // 60
    m = total_minutes % 60
    return f"{h}h {m}min"


def format_diff(minutes: int) -> str:
    if minutes == 0:
        return "a tiempo"
    if minutes > 0:
        return f"{minutes} min tarde"
    return f"{abs(minutes)} min antes"


def baby_age_months(chat_data: Dict[str, Any]) -> int:
    birthdate_str = chat_data.get("birthdate", SOFIA_BIRTHDATE)
    try:
        bd = datetime.fromisoformat(birthdate_str).date()
        today = now_local().date()
        months = (today.year - bd.year) * 12 + (today.month - bd.month)
        if today.day < bd.day:
            months -= 1
        return max(0, months)
    except Exception:
        return 7


def get_sleep_range(months: int) -> Tuple[float, float, float, float]:
    for m in sorted(SLEEP_RANGES.keys(), reverse=True):
        if months >= m:
            return SLEEP_RANGES[m]
    return SLEEP_RANGES[4]


def sleep_status_emoji(value_h: float, min_h: float, max_h: float) -> str:
    if value_h < min_h:
        return "⬇️ bajo"
    if value_h > max_h:
        return "⬆️ alto"
    return "✅ ok"


def baby_name(chat_data: Dict[str, Any]) -> str:
    name = (chat_data.get("baby_name") or "").strip()
    return name if name else "bebé"


def is_night_sleep_active(chat_data: Dict[str, Any]) -> bool:
    return str_to_dt(chat_data.get("active_night_sleep_start")) is not None


# =========================
# Teclado principal
# =========================
def keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        [
            [BUTTON_NAP, BUTTON_NIGHT, BUTTON_FEED],
            [BUTTON_STATUS, BUTTON_HISTORY, BUTTON_WEEKLY],
            [BUTTON_FOODS, BUTTON_MENU],
            [BUTTON_SLEEP_REC, BUTTON_SCHEDULE, BUTTON_TRANSITION],
            [BUTTON_UNDO],
        ],
        resize_keyboard=True,
        one_time_keyboard=False,
    )


def inline_nap() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[
        InlineKeyboardButton("▶️ Iniciar siesta", callback_data="nap_start"),
        InlineKeyboardButton("⏹️ Terminar siesta", callback_data="nap_end"),
    ]])


def inline_night() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[
        InlineKeyboardButton("▶️ Iniciar noche", callback_data="night_start"),
        InlineKeyboardButton("⏹️ Terminar noche", callback_data="night_end"),
    ]])


def inline_feed() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[
        InlineKeyboardButton("🍼 Biberón", callback_data="feed_biberon"),
        InlineKeyboardButton("🥣 Sólido", callback_data="feed_solido"),
    ]])


def inline_foods() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[
        InlineKeyboardButton("🍎 Nuevo alimento", callback_data="food_new"),
        InlineKeyboardButton("📋 Ver lista", callback_data="food_list"),
    ]])


def inline_menu() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[
        InlineKeyboardButton("📖 Ver menú", callback_data="menu_view"),
        InlineKeyboardButton("✏️ Guardar menú", callback_data="menu_save"),
        InlineKeyboardButton("🗑️ Eliminar", callback_data="menu_delete"),
    ]])


# =========================
# Persistencia
# =========================
def default_chat_state() -> Dict[str, Any]:
    return {
        "chat_id": None,
        "baby_name": "Sofía",
        "birthdate": SOFIA_BIRTHDATE,
        "last_biberon": None,
        "last_solido": None,
        "last_day_nap_end": None,
        "active_day_nap_start": None,
        "active_night_sleep_start": None,
        "history": [],
        "foods_tried": [],
        "pending_food_input": False,
        "weekly_menu": None,
        "pending_menu_input": False,
        "schedule_reminders_sent": {},
        "daily_messages": {
            "date": None,
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
        logger.warning("No se pudo leer JSON: %s", e)
        return {"chats": {}}


def save_data() -> None:
    temp = DATA_FILE.with_suffix(".tmp")
    temp.write_text(json.dumps(STATE, ensure_ascii=False, indent=2), encoding="utf-8")
    temp.replace(DATA_FILE)


def get_chat_state(chat_id: int) -> Dict[str, Any]:
    key = str(chat_id)
    chats = STATE.setdefault("chats", {})
    if key not in chats or not isinstance(chats[key], dict):
        chats[key] = default_chat_state()
    current = chats[key]
    base = default_chat_state()
    for k, v in base.items():
        if k not in current:
            current[k] = deepcopy(v)
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


def add_history_event(chat_data: Dict[str, Any], event_type: str, dt: datetime, extra: Optional[Dict] = None) -> None:
    cleanup_old_history(chat_data)
    item = {"type": event_type, "time": dt_to_str(dt)}
    if extra:
        item.update(extra)
    history = chat_data.setdefault("history", [])
    history.append(item)
    history.sort(key=lambda x: x.get("time", ""))


def undo_last_event(chat_data: Dict[str, Any]) -> str:
    """Elimina el último evento registrado y retorna mensaje descriptivo."""
    history = chat_data.get("history", [])
    if not history:
        return "No hay registros para anular."
    
    # Encontrar el último evento (por tiempo)
    last_event = max(history, key=lambda x: x.get("time", ""))
    history.remove(last_event)
    
    event_type = last_event.get("type", "desconocido")
    event_time = last_event.get("time", "")
    dt = str_to_dt(event_time)
    time_str = fmt_datetime(dt) if dt else event_time
    
    # Mapeo de tipos a emojis y nombres
    type_names = {
        "biberon": "🍼 Biberón",
        "solido": "🥣 Sólido",
        "day_nap_start": "😴 Siesta (inicio)",
        "day_nap_end": "😴 Siesta (fin)",
        "night_sleep_start": "🌙 Noche (inicio)",
        "night_sleep_end": "🌙 Noche (fin)",
    }
    
    event_name = type_names.get(event_type, event_type)
    save_data()
    return f"❌ Anulado: {event_name} a las {time_str}"


# =========================
# Horario dinamico
# =========================
def get_schedule_event_for_today(hour: int, minute: int) -> datetime:
    now = now_local()
    return now.replace(hour=hour, minute=minute, second=0, microsecond=0)


def find_next_schedule_event(chat_data: Dict[str, Any]) -> Optional[Tuple[datetime, str, str]]:
    """Devuelve (datetime, tipo, etiqueta) del próximo evento del horario."""
    now = now_local()
    today_events = []
    for h, m, tipo, label in SOFIA_SCHEDULE:
        dt = get_schedule_event_for_today(h, m)
        if dt > now:
            today_events.append((dt, tipo, label))
    if today_events:
        return min(today_events, key=lambda x: x[0])
    # Si no hay mas eventos hoy, el primero de manana
    tomorrow = now + timedelta(days=1)
    h, m, tipo, label = SOFIA_SCHEDULE[0]
    dt = tomorrow.replace(hour=h, minute=m, second=0, microsecond=0)
    return (dt, tipo, label)


def compare_with_schedule(event_type: str, actual_dt: datetime, chat_data: Dict[str, Any]) -> str:
    """Compara el momento registrado con el horario base y devuelve el desfase.
    Calcula el desfase desde el FIN de la ventana esperada."""
    now = actual_dt
    best = None
    best_diff = None
    best_offset = None
    
    for h_start, m_start, h_end, m_end, tipo, label in SOFIA_SCHEDULE:
        if tipo != event_type:
            continue
        
        # Hora de fin de la ventana esperada
        window_end = now.replace(hour=h_end, minute=m_end, second=0, microsecond=0)
        # Desfase = hora registrada - fin de ventana
        diff = int((now - window_end).total_seconds() / 60)
        
        if best_diff is None or abs(diff) < abs(best_diff):
            best_diff = diff
            best = label
            best_offset = diff
    
    # Guardar offset en chat_data para usar en notificaciones
    if best_offset is not None and chat_data is not None:
        offsets = chat_data.setdefault("event_offsets", {})
        offsets[event_type] = best_offset
        save_data()
    
    if best is None or best_diff is None:
        return ""
    return f"({best}: {format_diff(best_diff)})"


# =========================
# Siestas
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
        result.append({"start": start_dt, "end": end_dt, "duration": end_dt - start_dt})
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


def start_day_nap(chat_data: Dict[str, Any], start_dt: datetime) -> Tuple[bool, str]:
    if str_to_dt(chat_data.get("active_day_nap_start")):
        return False, "Ya hay una siesta en curso."
    if str_to_dt(chat_data.get("active_night_sleep_start")):
        return False, "Primero termina el sueño nocturno."
    chat_data["active_day_nap_start"] = dt_to_str(start_dt)
    add_history_event(chat_data, "day_nap_start", start_dt)
    save_data()
    diff = compare_with_schedule("nap", start_dt)
    return True, f"😴 Siesta iniciada a las {fmt_time(start_dt)} {diff}".strip()


def end_day_nap(chat_data: Dict[str, Any], end_dt: datetime) -> Tuple[bool, str]:
    start_dt = str_to_dt(chat_data.get("active_day_nap_start"))
    if not start_dt:
        return False, "No hay una siesta en curso."
    if end_dt < start_dt:
        return False, "La hora final no puede ser antes del inicio."
    duration = end_dt - start_dt
    chat_data["active_day_nap_start"] = None
    chat_data["last_day_nap_end"] = dt_to_str(end_dt)
    add_history_event(chat_data, "day_nap_end", end_dt, extra={
        "start_time": dt_to_str(start_dt),
        "duration_minutes": int(duration.total_seconds() // 60),
    })
    save_data()

    # Comprueba rango de siestas del dia
    months = baby_age_months(chat_data)
    nap_min, nap_max, _, _ = get_sleep_range(months)
    total = total_day_nap_today(chat_data)
    total_h = total.total_seconds() / 3600
    status = sleep_status_emoji(total_h, nap_min, nap_max)
    return True, (
        f"😴 Siesta terminada a las {fmt_time(end_dt)} ({format_duration(duration)})\n"
        f"Total siestas hoy: {format_duration(total)} {status} "
        f"(rango: {nap_min}h-{nap_max}h)"
    )


def start_night_sleep(chat_data: Dict[str, Any], start_dt: datetime) -> Tuple[bool, str]:
    if str_to_dt(chat_data.get("active_night_sleep_start")):
        return False, "Ya hay sueño nocturno en curso."
    if str_to_dt(chat_data.get("active_day_nap_start")):
        return False, "Primero termina la siesta actual."
    chat_data["active_night_sleep_start"] = dt_to_str(start_dt)
    add_history_event(chat_data, "night_sleep_start", start_dt)
    save_data()
    diff = compare_with_schedule("night", start_dt)
    return True, f"🌙 Sueño nocturno iniciado a las {fmt_time(start_dt)} {diff}".strip()


def end_night_sleep(chat_data: Dict[str, Any], end_dt: datetime) -> Tuple[bool, str]:
    start_dt = str_to_dt(chat_data.get("active_night_sleep_start"))
    if not start_dt:
        return False, "No hay sueño nocturno en curso."
    if end_dt < start_dt:
        return False, "La hora final no puede ser antes del inicio."
    duration = end_dt - start_dt
    chat_data["active_night_sleep_start"] = None
    add_history_event(chat_data, "night_sleep_end", end_dt, extra={
        "start_time": dt_to_str(start_dt),
        "duration_minutes": int(duration.total_seconds() // 60),
    })
    save_data()

    months = baby_age_months(chat_data)
    _, _, night_min, night_max = get_sleep_range(months)
    duration_h = duration.total_seconds() / 3600
    status = sleep_status_emoji(duration_h, night_min, night_max)
    return True, (
        f"🌙 Noche terminada a las {fmt_time(end_dt)} ({format_duration(duration)})\n"
        f"Sueño nocturno: {status} (rango: {night_min}h-{night_max}h)"
    )


# =========================
# Alimentacion
# =========================
def register_biberon(chat_data: Dict[str, Any], dt: datetime) -> str:
    chat_data["last_biberon"] = dt_to_str(dt)
    add_history_event(chat_data, "biberon", dt)
    save_data()
    diff = compare_with_schedule("biberon", dt, chat_data)
    return f"🍼 Biberón registrado a las {fmt_time(dt)} {diff}".strip()


def register_solido(chat_data: Dict[str, Any], dt: datetime) -> str:
    chat_data["last_solido"] = dt_to_str(dt)
    add_history_event(chat_data, "solido", dt)
    save_data()
    diff = compare_with_schedule("solido", dt, chat_data)
    return f"🥣 Sólido registrado a las {fmt_time(dt)} {diff}".strip()


# =========================
# Alimentos
# =========================
def add_food(chat_data: Dict[str, Any], food_name: str) -> str:
    foods = chat_data.setdefault("foods_tried", [])
    food_clean = food_name.strip().capitalize()
    if food_clean.lower() in [f.lower() for f in foods]:
        return f"⚠️ '{food_clean}' ya está en la lista."
    foods.append(food_clean)
    foods.sort()
    save_data()
    return f"✅ '{food_clean}' añadido."


def get_food_list(chat_data: Dict[str, Any]) -> str:
    foods = chat_data.get("foods_tried", [])
    if not foods:
        return "📋 Todavía no hay alimentos registrados."
    lines = [f"📋 Alimentos probados ({len(foods)}):"]
    for i, food in enumerate(foods, 1):
        lines.append(f"{i}. {food}")
    return "\n".join(lines)


# =========================
# Menu semanal
# =========================
def get_weekly_menu(chat_data: Dict[str, Any]) -> Optional[str]:
    return chat_data.get("weekly_menu")


def set_weekly_menu(chat_data: Dict[str, Any], text: str) -> None:
    chat_data["weekly_menu"] = text.strip()
    save_data()


def delete_weekly_menu(chat_data: Dict[str, Any]) -> None:
    chat_data["weekly_menu"] = None
    save_data()


# =========================
# Recomendacion de sueno
# =========================
def build_sleep_recommendation(chat_data: Dict[str, Any]) -> str:
    months = baby_age_months(chat_data)
    nap_min, nap_max, night_min, night_max = get_sleep_range(months)
    lines = [
        f"💤 Recomendación de sueño — {baby_name(chat_data)} ({months} meses)",
        "",
        f"😴 Siestas diarias: {nap_min}h – {nap_max}h",
        f"🌙 Sueño nocturno: {night_min}h – {night_max}h",
        "",
        "📊 Rangos por edad:",
        "  4m: siestas 3-5h · noche 10-12h",
        "  5m: siestas 3-4.5h · noche 10-12h",
        "  6m: siestas 2.5-4h · noche 10-12h",
        "  7m: siestas 2-3.5h · noche 10-12h",
        "  8m: siestas 2-3h · noche 10-12h",
        "  9m: siestas 1.5-3h · noche 10-12h",
        " 10m: siestas 1.5-2.5h · noche 10-12h",
        " 11m: siestas 1-2h · noche 10-12h",
        " 12m+: siestas 1-2h · noche 10-12h",
    ]
    return "\n".join(lines)


# =========================
# Horario base
# =========================
def build_schedule_text() -> str:
    """Construye el texto del horario base de Sofia."""
    lines = ["🕐 Horario base — Sofía", ""]
    
    for h_start, m_start, h_end, m_end, tipo, label in SOFIA_SCHEDULE:
        time_range = f"{h_start:02d}:{m_start:02d}-{h_end:02d}:{m_end:02d}"
        lines.append(f"{time_range}  {label}")
    
    lines.append("")
    lines.append("💡 Nota: El bot aprende de tus desfases y ajusta las notificaciones automáticamente.")
    return "\n".join(lines)


# =========================
# Transicion a solidos
# =========================
TRANSITION_TEXT = """🥄 Transición a sólidos — guía por edad

6 meses:
• Inicio de alimentación complementaria
• 1-2 comidas sólidas al día (puré suave)
• Base: lactancia o fórmula (4-5 tomas)
• Alimentos: verduras, frutas, cereales sin gluten

7-8 meses:
• 2 comidas sólidas + desayuno sólido
• Reducir a 3-4 biberones
• Textura: puré más grueso, aplastado
• Introducir proteína: pollo, pescado blanco, legumbres

9-10 meses:
• 3 comidas sólidas principales
• 2-3 biberones/tomas
• Textura: trozos pequeños blandos (BLW posible)
• Variedad amplia: huevo, carne roja, pasta

11-12 meses:
• 3 comidas + 1-2 snacks
• 2 biberones o equivalente lácteo
• Textura: familiar, trozos manejables
• Casi todo permitido excepto miel y sal

12 meses+:
• 3 comidas + lácteos integrados (leche entera)
• Sin biberón necesario
• Dieta similar a la familiar adaptada"""


# =========================
# Resumen semanal
# =========================
def build_weekly_summary(chat_data: Dict[str, Any]) -> str:
    now = now_local()
    week_ago = now - timedelta(days=7)
    night_sleeps = []
    daily_naps: Dict[str, timedelta] = {}
    biberones: List[str] = []
    solidos: List[str] = []

    for item in chat_data.get("history", []):
        dt = str_to_dt(item.get("time"))
        if not dt or dt < week_ago:
            continue
        if item.get("type") == "night_sleep_end":
            start_dt = str_to_dt(item.get("start_time"))
            if start_dt:
                night_sleeps.append({"start": start_dt, "end": dt, "duration": dt - start_dt})
        elif item.get("type") == "day_nap_end":
            start_dt = str_to_dt(item.get("start_time"))
            if start_dt:
                day_key = start_dt.date().isoformat()
                daily_naps[day_key] = daily_naps.get(day_key, timedelta()) + (dt - start_dt)
        elif item.get("type") == "biberon":
            biberones.append(dt.strftime("%d/%m %H:%M"))
        elif item.get("type") == "solido":
            solidos.append(dt.strftime("%d/%m %H:%M"))

    months = baby_age_months(chat_data)
    nap_min, nap_max, night_min, night_max = get_sleep_range(months)

    lines = [f"📈 Resumen semanal — {baby_name(chat_data)} ({months} meses)", ""]

    # Sueno nocturno
    lines.append("🌙 Sueño nocturno:")
    if night_sleeps:
        total_night = timedelta()
        for ns in night_sleeps:
            total_night += ns["duration"]
            dur_h = ns["duration"].total_seconds() / 3600
            status = sleep_status_emoji(dur_h, night_min, night_max)
            lines.append(f"  {ns['start'].strftime('%d/%m')} — {fmt_time(ns['start'])} a {fmt_time(ns['end'])} ({format_duration(ns['duration'])}) {status}")
        avg_h = (total_night / len(night_sleeps)).total_seconds() / 3600
        avg_status = sleep_status_emoji(avg_h, night_min, night_max)
        lines.append(f"  Promedio: {format_duration(total_night / len(night_sleeps))} {avg_status}")
        lines.append(f"  Rango recomendado: {night_min}h – {night_max}h")
    else:
        lines.append("  Sin registros.")

    lines.append("")

    # Siestas
    lines.append("😴 Siestas por día:")
    if daily_naps:
        total_nap_week = timedelta()
        for day_key in sorted(daily_naps.keys()):
            day_dt = datetime.fromisoformat(day_key)
            duration = daily_naps[day_key]
            total_nap_week += duration
            dur_h = duration.total_seconds() / 3600
            status = sleep_status_emoji(dur_h, nap_min, nap_max)
            lines.append(f"  {day_dt.strftime('%d/%m')} — {format_duration(duration)} {status}")
        avg = total_nap_week / len(daily_naps)
        avg_h = avg.total_seconds() / 3600
        avg_status = sleep_status_emoji(avg_h, nap_min, nap_max)
        lines.append(f"  Promedio: {format_duration(avg)} {avg_status}")
        lines.append(f"  Rango recomendado: {nap_min}h – {nap_max}h")
    else:
        lines.append("  Sin registros.")

    lines.append("")

    # Alimentacion
    lines.append(f"🍼 Biberones esta semana: {len(biberones)}")
    lines.append(f"🥣 Sólidos esta semana: {len(solidos)}")

    return "\n".join(lines)


# =========================
# Estado
# =========================
def build_status_text(chat_data: Dict[str, Any]) -> str:
    active_day_nap = str_to_dt(chat_data.get("active_day_nap_start"))
    active_night_sleep = str_to_dt(chat_data.get("active_night_sleep_start"))
    total_naps = total_day_nap_today(chat_data)
    last_biberon = str_to_dt(chat_data.get("last_biberon"))
    last_solido = str_to_dt(chat_data.get("last_solido"))

    months = baby_age_months(chat_data)
    nap_min, nap_max, _, _ = get_sleep_range(months)
    total_h = total_naps.total_seconds() / 3600
    nap_status = sleep_status_emoji(total_h, nap_min, nap_max)

    next_ev = find_next_schedule_event(chat_data)
    next_text = f"{fmt_time(next_ev[0])} — {next_ev[2]}" if next_ev else "—"

    night_state = (
        f"🌙 En curso desde {fmt_time(active_night_sleep)}"
        if active_night_sleep else "🌙 No activo"
    )
    nap_state = (
        f"😴 En curso desde {fmt_time(active_day_nap)}"
        if active_day_nap else f"😴 Última siesta: {fmt_datetime(str_to_dt(chat_data.get('last_day_nap_end')))}"
    )

    return "\n".join([
        f"👶 {baby_name(chat_data)} — {months} meses", "",
        f"🍼 Último biberón: {fmt_datetime(last_biberon)}",
        f"🥣 Último sólido: {fmt_datetime(last_solido)}", "",
        nap_state,
        f"🕒 Siestas hoy: {format_duration(total_naps)} {nap_status} (rango {nap_min}h-{nap_max}h)", "",
        night_state, "",
        f"⏰ Próximo: {next_text}",
    ])


# =========================
# Historial
# =========================
def build_today_history_text(chat_data: Dict[str, Any]) -> str:
    events = get_today_events(chat_data)
    active_day_nap = str_to_dt(chat_data.get("active_day_nap_start"))
    active_night_sleep = str_to_dt(chat_data.get("active_night_sleep_start"))

    if not events and not active_day_nap and not active_night_sleep:
        return "📅 Hoy no hay registros todavía."

    biberones, solidos, day_naps, night_sleeps = [], [], [], []

    for item in events:
        t = item.get("type")
        dt = item["dt"]
        if t == "biberon":
            biberones.append(f"🍼 {dt.strftime('%H:%M')}")
        elif t == "solido":
            solidos.append(f"🥣 {dt.strftime('%H:%M')}")
        elif t == "day_nap_end":
            start_dt = str_to_dt(item.get("start_time"))
            if start_dt:
                day_naps.append(f"😴 {start_dt.strftime('%H:%M')} - {dt.strftime('%H:%M')} ({format_duration(dt - start_dt)})")
        elif t == "night_sleep_end":
            start_dt = str_to_dt(item.get("start_time"))
            if start_dt:
                night_sleeps.append(f"🌙 {start_dt.strftime('%d/%m %H:%M')} - {dt.strftime('%d/%m %H:%M')} ({format_duration(dt - start_dt)})")

    if active_day_nap and active_day_nap.date() == now_local().date():
        day_naps.append(f"😴 {active_day_nap.strftime('%H:%M')} - en curso")
    if active_night_sleep:
        night_sleeps.append(f"🌙 {active_night_sleep.strftime('%d/%m %H:%M')} - en curso")

    months = baby_age_months(chat_data)
    nap_min, nap_max, _, _ = get_sleep_range(months)
    total_naps = total_day_nap_today(chat_data)
    total_h = total_naps.total_seconds() / 3600
    nap_status = sleep_status_emoji(total_h, nap_min, nap_max)

    lines = ["📅 Historial de hoy", ""]
    lines.append("🍼 Biberones:")
    lines.extend(biberones if biberones else ["—"])
    lines.append("\n🥣 Sólidos:")
    lines.extend(solidos if solidos else ["—"])
    lines.append(f"\n😴 Siestas ({format_duration(total_naps)} {nap_status} | rango {nap_min}h-{nap_max}h):")
    lines.extend(day_naps if day_naps else ["—"])
    lines.append("\n🌙 Sueño nocturno:")
    lines.extend(night_sleeps if night_sleeps else ["—"])
    return "\n".join(lines)


# =========================
# Envios
# =========================
async def send_with_keyboard(update: Update, text: str) -> None:
    if not update.message:
        return
    if len(text) <= 4096:
        await update.message.reply_text(text, reply_markup=keyboard())
    else:
        chunks = [text[i:i+4096] for i in range(0, len(text), 4096)]
        for i, chunk in enumerate(chunks):
            mk = keyboard() if i == len(chunks) - 1 else None
            await update.message.reply_text(chunk, reply_markup=mk)


async def send_to_chat(context: ContextTypes.DEFAULT_TYPE, chat_id: int, text: str) -> None:
    await context.bot.send_message(chat_id=chat_id, text=text, reply_markup=keyboard())


# =========================
# Callbacks inline
# =========================
async def callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if not query or not update.effective_chat:
        return
    await query.answer()
    chat_data = get_chat_state(update.effective_chat.id)
    data = query.data

    if data == "nap_start":
        ok, msg = start_day_nap(chat_data, now_local())
        await query.edit_message_text(msg)
    elif data == "nap_end":
        ok, msg = end_day_nap(chat_data, now_local())
        await query.edit_message_text(msg)
    elif data == "night_start":
        ok, msg = start_night_sleep(chat_data, now_local())
        await query.edit_message_text(msg)
    elif data == "night_end":
        ok, msg = end_night_sleep(chat_data, now_local())
        await query.edit_message_text(msg)
    elif data == "feed_biberon":
        msg = register_biberon(chat_data, now_local())
        await query.edit_message_text(msg)
    elif data == "feed_solido":
        msg = register_solido(chat_data, now_local())
        await query.edit_message_text(msg)
    elif data == "food_new":
        chat_data["pending_food_input"] = True
        save_data()
        await query.edit_message_text("🍎 Escribe el nombre del alimento:")
    elif data == "food_list":
        await query.edit_message_text(get_food_list(chat_data))
    elif data == "menu_view":
        menu = get_weekly_menu(chat_data)
        await query.edit_message_text(f"🗓️ Menú:\n\n{menu}" if menu else "No hay menú guardado.")
    elif data == "menu_save":
        chat_data["pending_menu_input"] = True
        save_data()
        await query.edit_message_text("🗓️ Pega el menú semanal ahora:")
    elif data == "menu_delete":
        delete_weekly_menu(chat_data)
        await query.edit_message_text("🗑️ Menú eliminado.")


# =========================
# Comandos
# =========================
async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.effective_chat:
        return
    chat_data = get_chat_state(update.effective_chat.id)
    save_data()
    await send_with_keyboard(update, f"Hola! Bot de {baby_name(chat_data)} activo. Usa /help para ver comandos.")


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await send_with_keyboard(update,
        "Botones principales:\n"
        "😴 Siesta → Iniciar/Terminar\n"
        "🌙 Noche → Iniciar/Terminar\n"
        "🍼 Alimentación → Biberón/Sólido\n"
        "🍎 Alimentos → Nuevo/Ver lista\n"
        "🗓️ Menú → Ver/Guardar/Eliminar\n\n"
        "Comandos manuales con hora:\n"
        "/napstart HH:MM · /napend HH:MM\n"
        "/nightstart HH:MM · /nightend HH:MM\n"
        "/biberon HH:MM · /solido HH:MM\n"
        "/setname Nombre · /setbirthdate YYYY-MM-DD"
    )


async def setname_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.effective_chat or not context.args:
        await send_with_keyboard(update, "Usa /setname Nombre")
        return
    chat_data = get_chat_state(update.effective_chat.id)
    chat_data["baby_name"] = " ".join(context.args).strip()
    save_data()
    await send_with_keyboard(update, f"Nombre guardado: {chat_data['baby_name']}")


async def setbirthdate_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.effective_chat or not context.args:
        await send_with_keyboard(update, "Usa /setbirthdate YYYY-MM-DD")
        return
    chat_data = get_chat_state(update.effective_chat.id)
    try:
        datetime.fromisoformat(context.args[0])
        chat_data["birthdate"] = context.args[0]
        save_data()
        months = baby_age_months(chat_data)
        await send_with_keyboard(update, f"Fecha guardada. {baby_name(chat_data)} tiene {months} meses.")
    except ValueError:
        await send_with_keyboard(update, "Formato incorrecto. Usa YYYY-MM-DD (ej: 2024-09-15)")


async def napstart_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.effective_chat:
        return
    chat_data = get_chat_state(update.effective_chat.id)
    dt = (parse_manual_time(context.args[0]) if context.args else None) or now_local()
    ok, msg = start_day_nap(chat_data, dt)
    await send_with_keyboard(update, msg)


async def napend_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.effective_chat:
        return
    chat_data = get_chat_state(update.effective_chat.id)
    dt = (parse_manual_time(context.args[0]) if context.args else None) or now_local()
    ok, msg = end_day_nap(chat_data, dt)
    await send_with_keyboard(update, msg)


async def nightstart_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.effective_chat:
        return
    chat_data = get_chat_state(update.effective_chat.id)
    dt = (parse_manual_time(context.args[0]) if context.args else None) or now_local()
    ok, msg = start_night_sleep(chat_data, dt)
    await send_with_keyboard(update, msg)


async def nightend_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.effective_chat:
        return
    chat_data = get_chat_state(update.effective_chat.id)
    dt = (parse_manual_time(context.args[0]) if context.args else None) or now_local()
    ok, msg = end_night_sleep(chat_data, dt)
    await send_with_keyboard(update, msg)


async def biberon_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.effective_chat:
        return
    chat_data = get_chat_state(update.effective_chat.id)
    dt = (parse_manual_time(context.args[0]) if context.args else None) or now_local()
    msg = register_biberon(chat_data, dt)
    await send_with_keyboard(update, msg)


async def solido_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.effective_chat:
        return
    chat_data = get_chat_state(update.effective_chat.id)
    dt = (parse_manual_time(context.args[0]) if context.args else None) or now_local()
    msg = register_solido(chat_data, dt)
    await send_with_keyboard(update, msg)


async def status_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.effective_chat:
        return
    await send_with_keyboard(update, build_status_text(get_chat_state(update.effective_chat.id)))


async def history_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.effective_chat:
        return
    await send_with_keyboard(update, build_today_history_text(get_chat_state(update.effective_chat.id)))


async def weekly_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.effective_chat:
        return
    await send_with_keyboard(update, build_weekly_summary(get_chat_state(update.effective_chat.id)))


async def sleep_rec_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.effective_chat:
        return
    await send_with_keyboard(update, build_sleep_recommendation(get_chat_state(update.effective_chat.id)))


async def schedule_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.effective_chat:
        return
    await send_with_keyboard(update, build_schedule_text())


async def undo_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.effective_chat:
        return
    chat_data = get_chat_state(update.effective_chat.id)
    msg = undo_last_event(chat_data)
    await send_with_keyboard(update, msg)


async def transition_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.effective_chat:
        return
    await send_with_keyboard(update, TRANSITION_TEXT)


# =========================
# Botones del teclado principal
# =========================
async def button_nap(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message:
        return
    await update.message.reply_text("😴 Siesta:", reply_markup=inline_nap())


async def button_night(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message:
        return
    await update.message.reply_text("🌙 Noche:", reply_markup=inline_night())


async def button_feed(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message:
        return
    await update.message.reply_text("🍼 Alimentación:", reply_markup=inline_feed())


async def button_foods(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message:
        return
    await update.message.reply_text("🍎 Alimentos:", reply_markup=inline_foods())


async def button_menu(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message:
        return
    await update.message.reply_text("🗓️ Menú semanal:", reply_markup=inline_menu())


# =========================
# Texto libre
# =========================
def parse_manual_time(text: str) -> Optional[datetime]:
    try:
        parts = text.strip().split(":")
        if len(parts) != 2:
            return None
        h, m = int(parts[0]), int(parts[1])
        if not (0 <= h <= 23 and 0 <= m <= 59):
            return None
        now = now_local()
        return now.replace(hour=h, minute=m, second=0, microsecond=0)
    except Exception:
        return None


async def unknown_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.effective_chat or not update.message:
        return
    chat_data = get_chat_state(update.effective_chat.id)

    if chat_data.get("pending_menu_input"):
        chat_data["pending_menu_input"] = False
        set_weekly_menu(chat_data, update.message.text.strip())
        await send_with_keyboard(update, "✅ Menú semanal guardado.")
        return

    if chat_data.get("pending_food_input"):
        chat_data["pending_food_input"] = False
        result = add_food(chat_data, update.message.text.strip())
        await send_with_keyboard(update, result)
        return

    await send_with_keyboard(update, "Usa /help para ver los comandos.")


# =========================
# Recordatorios automaticos (horario de Sofia)
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

        cleanup_old_history(chat_data)
        daily = chat_data.setdefault("daily_messages", {})
        sent = chat_data.setdefault("schedule_reminders_sent", {})

        # Reseteo diario
        if daily.get("date") != current_date_str:
            daily["date"] = current_date_str
            chat_data["schedule_reminders_sent"] = {}
            sent = chat_data["schedule_reminders_sent"]
            changed = True

        night_active = is_night_sleep_active(chat_data)
        
        # Obtener offset de eventos registrados (desfase)
        offsets = chat_data.get("event_offsets", {})  # {"biberon": 45, "solido": -10, ...}

        for h_start, m_start, h_end, m_end, tipo, label in SOFIA_SCHEDULE:
            event_key = f"{h_start:02d}{m_start:02d}_{tipo}"
            if sent.get(event_key):
                continue

            scheduled_dt = get_schedule_event_for_today(h_start, m_start)
            
            # Aplicar offset si existe para este tipo de evento
            offset_minutes = offsets.get(tipo, 0)
            scheduled_dt = scheduled_dt + timedelta(minutes=offset_minutes)
            
            remind_dt = scheduled_dt - timedelta(minutes=15)

            # No avisar siestas si hay sueno nocturno activo
            if tipo == "nap" and night_active:
                continue
            
            # No avisar si estamos en la ventana de vigilia antes de dormir (20:00 - 2.5h)
            bedtime_dt = get_schedule_event_for_today(20, 0)
            vigilia_start = bedtime_dt - timedelta(minutes=MAX_AWAKE_BEFORE_BEDTIME)
            if current_now >= vigilia_start and current_now < bedtime_dt and tipo in ["biberon", "solido", "nap"]:
                continue

            if current_now >= remind_dt and current_now < scheduled_dt and not sent.get(f"{event_key}_15"):
                await send_to_chat(context, chat_id, f"⏰ En 15 min: {label}")
                sent[f"{event_key}_15"] = True
                changed = True
            elif current_now >= scheduled_dt and not sent.get(f"{event_key}_due"):
                await send_to_chat(context, chat_id, f"🔔 Ahora toca: {label}")
                sent[f"{event_key}_due"] = True
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
    application.add_handler(CommandHandler("setbirthdate", setbirthdate_command))
    application.add_handler(CommandHandler("napstart", napstart_command))
    application.add_handler(CommandHandler("napend", napend_command))
    application.add_handler(CommandHandler("nightstart", nightstart_command))
    application.add_handler(CommandHandler("nightend", nightend_command))
    application.add_handler(CommandHandler("biberon", biberon_command))
    application.add_handler(CommandHandler("solido", solido_command))
    application.add_handler(CommandHandler("status", status_command))
    application.add_handler(CommandHandler("history", history_command))
    application.add_handler(CommandHandler("weekly", weekly_command))
    application.add_handler(CommandHandler("sleeprec", sleep_rec_command))
    application.add_handler(CommandHandler("schedule", schedule_command))
    application.add_handler(CommandHandler("undo", undo_command))
    application.add_handler(CommandHandler("transition", transition_command))

    application.add_handler(CallbackQueryHandler(callback_handler))

    application.add_handler(MessageHandler(filters.Regex(f"^{BUTTON_NAP}$"), button_nap))
    application.add_handler(MessageHandler(filters.Regex(f"^{BUTTON_NIGHT}$"), button_night))
    application.add_handler(MessageHandler(filters.Regex(f"^{BUTTON_FEED}$"), button_feed))
    application.add_handler(MessageHandler(filters.Regex(f"^{BUTTON_STATUS}$"), status_command))
    application.add_handler(MessageHandler(filters.Regex(f"^{BUTTON_HISTORY}$"), history_command))
    application.add_handler(MessageHandler(filters.Regex(f"^{BUTTON_WEEKLY}$"), weekly_command))
    application.add_handler(MessageHandler(filters.Regex(f"^{BUTTON_FOODS}$"), button_foods))
    application.add_handler(MessageHandler(filters.Regex(f"^{BUTTON_MENU}$"), button_menu))
    application.add_handler(MessageHandler(filters.Regex(f"^{BUTTON_SLEEP_REC}$"), sleep_rec_command))
    application.add_handler(MessageHandler(filters.Regex(f"^{BUTTON_SCHEDULE}$"), schedule_command))
    application.add_handler(MessageHandler(filters.Regex(f"^{BUTTON_UNDO}$"), undo_command))
    application.add_handler(MessageHandler(filters.Regex(f"^{BUTTON_TRANSITION}$"), transition_command))

    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, unknown_text))

    application.run_polling(drop_pending_updates=False)


if __name__ == "__main__":
    main()
