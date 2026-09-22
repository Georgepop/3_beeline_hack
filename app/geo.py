"""Геокодирование адресов.

Порядок: Nominatim (с кэшем в файл) → fallback по району (центр района) → None.
rate-limit ~1.1 сек/запрос; кэш в data/geocode_cache.json (gitignored).
"""

import json
import re
import time
from pathlib import Path

import requests

from app.config import get_settings

CACHE_PATH = Path("data/geocode_cache.json")
_cache: dict[str, list[float] | None] = {}


def _load_cache() -> None:
    global _cache
    if _cache:
        return
    try:
        if CACHE_PATH.exists():
            _cache = json.loads(CACHE_PATH.read_text(encoding="utf-8"))
    except Exception:
        _cache = {}


def _save_cache() -> None:
    try:
        CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
        CACHE_PATH.write_text(json.dumps(_cache, ensure_ascii=False), encoding="utf-8")
    except Exception:
        pass


# Центры районов Москвы (fallback). Точные координаты даст Nominatim.
DISTRICT_CENTERS: dict[str, tuple[float, float]] = {
    "Кузьминки": (55.705, 37.770),
    "Таганский": (55.741, 37.656),
    "Текстильщики": (55.707, 37.732),
    "Рязанский": (55.714, 37.762),
    "Южнопортовый": (55.710, 37.685),
    "Нижегородский": (55.731, 37.728),
    "Лефортово": (55.760, 37.707),
    "Выхино": (55.715, 37.817),
    "Басманный": (55.773, 37.677),
    "Домодедово": (55.441, 37.753),
    "Орехово Борисово Южное": (55.613, 37.734),
    "Зябликово": (55.623, 37.745),
    "Москворечье - Сабурово": (55.657, 37.720),
    "Бирюлево Восточное": (55.609, 37.672),
    "Кашира": (54.853, 38.167),
    "Братеево": (55.635, 37.761),
    "Царицыно": (55.635, 37.675),
    "Орехово Борисово Северное": (55.614, 37.710),
    "Бирюлево Западное": (55.605, 37.650),
    "Ступино": (54.892, 38.078),
    "Даниловский": (55.713, 37.633),
    "Академический": (55.684, 37.577),
    "Котловка": (55.675, 37.615),
    "Зюзино": (55.655, 37.586),
    "Хамовники": (55.731, 37.569),
    "Нагатино - Садовники": (55.671, 37.666),
    "Замоскворечье": (55.730, 37.631),
    "Нагатинский Затон": (55.663, 37.703),
    "Нагорный": (55.671, 37.605),
    "Донской": (55.714, 37.607),
    "Гагаринский": (55.698, 37.559),
}


def normalize_address(address: str) -> str:
    a = re.sub(r"(?i)^г\.?\s*Москва\s*,?\s*", "", address.strip())
    a = re.sub(r"\s+", " ", a)
    a = re.sub(r"(?i)\bд\.?\s*", " ", a)                # "д 83" -> "83"
    a = re.sub(r"([0-9])\s*([сСкК])\s*([0-9])", r"\1\2\3", a)  # "83с 4" -> "83с4"
    a = re.sub(r"(?i)\bс\b", " ", a)
    return re.sub(r"\s+", " ", a).strip()


def _query_nominatim(q: str) -> list[float] | None:
    s = get_settings()
    try:
        r = requests.get(
            s.nominatim_url + "/search",
            params={"q": q, "format": "json", "limit": 1},
            timeout=10,
            headers={"User-Agent": "beeline-field-service/1.0 (hackathon)"},
        )
        if r.ok:
            data = r.json()
            if data:
                return [float(data[0]["lat"]), float(data[0]["lon"])]
    except Exception:
        pass
    finally:
        time.sleep(1.1)  # политика Nominatim: не чаще ~1 запроса в секунду
    return None


def _district_fallback(address: str, district: str = "") -> list[float] | None:
    for name, coord in DISTRICT_CENTERS.items():
        if district and name.lower() in district.lower():
            return list(coord)
    for name, coord in DISTRICT_CENTERS.items():
        if name.lower() in address.lower():
            return list(coord)
    return None


def geocode(address: str, district: str = "") -> list[float] | None:
    """Возвращает [lat, lng] или None."""
    key = (address.strip(), district.strip())
    _load_cache()
    stored_key = key[0]
    if stored_key in _cache:
        return _cache[stored_key]

    result = None
    if get_settings().geocoder == "nominatim":
        result = _query_nominatim("Москва, " + normalize_address(stored_key))
        if not result:
            result = _query_nominatim(normalize_address(stored_key))
    if not result:
        result = _district_fallback(stored_key, district)

    _cache[stored_key] = result
    _save_cache()
    return result