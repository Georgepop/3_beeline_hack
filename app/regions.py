"""Справочники предметной области: регионы, навыки, нормативы, транспорт."""

from app.config import get_settings
from app.schemas import LatLng

# --- Регионы: id, название, адрес офиса (старт точка инженеров) ---
REGIONS = {
    "vostok": {
        "id": "vostok",
        "name": "Восток",
        "office_address": "г. Москва, ул Юных Ленинцев, д 83с 4",
        # координаты — предварительные (Phase 3 перегеокодирует с кэшем)
        "office": LatLng(lat=55.679, lng=37.766, name="Офис Восток"),
    },
    "yugo_vostok": {
        "id": "yugo_vostok",
        "name": "Юго-восток",
        "office_address": "г. Москва, ул Бирюлёвская, д 1с1",
        "office": LatLng(lat=55.590, lng=37.683, name="Офис Юго-восток"),
    },
    "yugocentr": {
        "id": "yugocentr",
        "name": "Югоцентр",
        "office_address": "г.Москва проезд Симферопольский, д.7",
        "office": LatLng(lat=55.652, lng=37.605, name="Офис Югоцентр"),
    },
}

# --- Навыки (3 квалификации из ТЗ) ---
SKILLS = {
    "podklyuchenie": {"label": "Подключение и дозаказы"},
    "lokalnye": {"label": "Локальные работы"},
    "avariynye": {"label": "Аварийные работы"},
}

# Маппинг типа заявки BK -> навык
BK_TO_SKILL = {
    "Подключение": "podklyuchenie",
    "Дозаказ": "podklyuchenie",
    "Локальная заявка": "lokalnye",
    "Глобальная проблема": "avariynye",
}
# HD-типы, маркирующие аварийную
HD_URGENT = {"Авария"}

# Нормативы длительности работ, мин (работы + документы; дорога отдельно)
BK_NORM_MIN = {
    "Подключение": 100,
    "Дозаказ": 90,
    "Локальная заявка": 50,
    "Глобальная проблема": 40,
}
DEFAULT_NORM_MIN = 60

# --- Транспорт ---
TRANSPORT = {
    "auto": {"label": "Автомобиль"},
    "transit": {"label": "Общественный транспорт"},
    "bike": {"label": "Велосипед"},
    "walk": {"label": "Пешком"},
}


def norm_min(bk_type: str) -> int:
    return BK_NORM_MIN.get(bk_type, DEFAULT_NORM_MIN)


def skill_of(bk_type: str, hd_type: str = "") -> str:
    skill = BK_TO_SKILL.get(bk_type)
    if not skill and hd_type in HD_URGENT:
        skill = "avariynye"
    return skill or "lokalnye"


def urgency_of(bk_type: str, hd_type: str = "") -> str:
    if bk_type == "Глобальная проблема" or hd_type in HD_URGENT:
        return "urgent"
    return "normal"


def shift_bounds() -> tuple[str, str]:
    s = get_settings()
    return s.shift_start, s.shift_end


def speed_kmh(transport: str) -> float:
    return get_settings().speed_kmh.get(transport, 5.0)