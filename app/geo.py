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
    a = address.strip()
    # Срезаем ведущие населённые пункты: «Город Москва, …», «г. Москва, …», «Москва, …»
    a = re.sub(r"(?i)^(г(ород)?\.?\s*)?(город\s+)?москва[\s,]+", "", a)
    a = re.sub(r"\s+", " ", a)
    a = re.sub(r"(?i)\bд\.?\s*", " ", a)                # "д 83" -> "83"
    a = re.sub(r"(?i)\bстр(оение)?\.?\s*(\d+)", r"с\2", a)    # "стр. 2"/"стр2" -> "с2"
    a = re.sub(r"(?i)\bкорп(ус)?\.?\s*(\d+)", r"к\2", a)      # "корп. 4" -> "к4"
    a = re.sub(r"(?i)\bк\.?\s*(\d+)", r"к\1", a)        # "к 4" -> "к4"
    a = re.sub(r"([0-9])\s*([сСкК])\s*([0-9])", r"\1\2\3", a)  # "83с 4" -> "83с4"
    a = re.sub(r"(?i)\bс\b", " ", a)
    return re.sub(r"\s+", " ", a).strip()


def clean_display_address(address: str) -> str:
    """Приводим ведущий населённый пункт к «Москва»: «г. Город Москва, …» → «Москва, …»."""
    a = re.sub(r"(?i)^(г(ород)?\.?\s*)?(город\s+)?москва\b", "Москва", (address or "").strip(), count=1)
    return re.sub(r"\s+", " ", a).strip()


_TYPE_WORDS = {
    "улица", "улицы", "улице", "проспект", "проспекта", "бульвар", "бульвара",
    "проезд", "проезда", "шоссе", "набережная", "набережной", "переулок",
    "площадь", "аллея", "линия", "квартал", "дом", "корпус", "строение", "город",
}

_ABBREV_TYPES = {"пр-кт": "проспект", "просп": "проспект", "б-р": "бульвар", "пр-д": "проезд", "наб": "набережная", "ш": "шоссе"}
_ABBREV_EXPAND = re.compile(r"(?i)\b(пр-кт|просп|б-р|пр-д|наб|ш)(?![а-яё])\.?\s*")


def _expand_abbrevs(a: str) -> str:
    """Раскрываем сокращения типов улиц, которые Nominatim/Photon не понимают."""
    a = _ABBREV_EXPAND.sub(lambda m: _ABBREV_TYPES[m.group(1).lower()] + " ", a)
    return re.sub(r"\s+", " ", a).strip()


def _split_city(address: str) -> tuple[str, str]:
    """(населённый пункт, остаток). «Город Москва, …»/«Москва, …»/«Домодедово, …»"""
    m = re.match(r"(?i)^(?:г(ород|\.)?\s*)?([а-яёa-z0-9 -]+?)[,\s]+(.+)$", address, re.S)
    if not m:
        return "Москва", re.sub(r"(?i)^(г(ород)?\.?\s*)?(город\s+)?москва[\s,]+", "", address)
    return m.group(2).strip(), m.group(3).strip()


def _split_house(rest: str) -> tuple[str, str]:
    """(улица, номер дома). «пр-кт.60-летия Октября, д. 17» -> («пр-кт.60-летия Октября», «17»)"""
    m = re.match(
        r"(?is)^(.*?)[,\s]+(?:д\.\s*)?([0-9][0-9а-яёa-zA-Z]*(?:\s*[кК]\s?[0-9][0-9а-яёa-zA-Z]*)?)\s*$",
        rest,
    )
    if m:
        return m.group(1).strip(), m.group(2).strip()
    return rest, ""


def _query_variants(address: str) -> list[str]:
    """Запросы к геокодерам в порядке предпочтения (для одного адреса)."""
    city, rest = _split_city(address)
    street, house = _split_house(rest)
    qs = [f"{city}, {rest.strip(', ')}"]                       # как есть
    qs.append(f"{city}, {_expand_abbrevs(rest.strip(', '))}")  # с раскрытыми сокращениями
    if street:
        # только улица + дом без «д.»/«к N» (номера корпусов часто ломают поиск)
        qs.append(f"{city}, {_expand_abbrevs(street)}, {house}" if house else f"{city}, {_expand_abbrevs(street)}")
    seen, out = set(), []
    for q in qs:
        if q not in seen:
            seen.add(q)
            out.append(q)
    return out


def _norm_tokens(s: str) -> set[str]:
    s = (s or "").lower()
    for w in _TYPE_WORDS:
        s = re.sub(rf"(?<![а-яёa-z]{len(w)}){re.escape(w)}(?![а-яёa-z])", " ", s)
    s = re.sub(r"[^a-zа-яё0-9]+", " ", s)
    return {t for t in s.split() if t}


def _street_matches(query: str, photon_street: str) -> bool:
    """Совпала ли улица в ответе Photon с запрашиваемой (иначе это ложное срабатывание)."""
    city, rest = _split_city(query)
    street, _ = _split_house(rest)
    a = _norm_tokens(street)
    b = _norm_tokens(photon_street)
    if not a or not b or len(a & b) == 0:
        return False
    da = {t for t in a if any(c.isdigit() for c in t)}
    db = {t for t in b if any(c.isdigit() for c in t)}
    if da and db and not (da & db):
        return False
    return True


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


def _query_photon(q: str) -> tuple[list[float] | None, str]:
    """Photon (бесплатный, без ключа) как дополнение к Nominatim. Вернёт ([lat,lng], street) или (None, '')."""
    try:
        r = requests.get(
            "https://photon.komoot.io/api/",
            params={"q": q, "limit": 1},
            timeout=12,
            headers={"User-Agent": "beeline-field-service/1.0 (hackathon)"},
        )
        if r.ok:
            feat = (r.json().get("features") or [None])[0]
            if feat:
                c = feat["geometry"]["coordinates"]
                return [float(c[1]), float(c[0])], feat["properties"].get("street") or ""
    except Exception:
        pass
    finally:
        time.sleep(0.3)
    return None, ""


def _district_fallback(address: str, district: str = "") -> list[float] | None:
    for name, coord in DISTRICT_CENTERS.items():
        if district and name.lower() in district.lower():
            return list(coord)
    for name, coord in DISTRICT_CENTERS.items():
        if name.lower() in address.lower():
            return list(coord)
    return None


_MOSCOW_CENTER = (55.7558, 37.6173)


def _haversine_km(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    import math

    r = 6371.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lng2 - lng1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def _plausible(coord: list[float] | None, address: str) -> bool:
    """Отбрасываем ложные срабатывания: «Москва, …» должно геокодиться в пределах Москвы."""
    if not coord:
        return False
    city, _ = _split_city(address)
    if city.lower() == "москва":
        return _haversine_km(coord[0], coord[1], *_MOSCOW_CENTER) <= 65.0
    return True


def geocode(address: str, district: str = "") -> list[float] | None:
    """Возвращает [lat, lng] или None."""
    key = (address.strip(), district.strip())
    _load_cache()
    stored_key = key[0]
    if stored_key in _cache:
        return _cache[stored_key]

    result = None
    if get_settings().geocoder == "nominatim":
        for q in _query_variants(stored_key):
            result = _query_nominatim(q)
            if result and _plausible(result, stored_key):
                break
            result = None
    if not result and get_settings().geocoder == "nominatim":
        for q in _query_variants(stored_key):
            coord, p_street = _query_photon(q)
            if coord and _street_matches(q, p_street) and _plausible(coord, stored_key):
                result = coord
                break
    if not result:
        result = _district_fallback(stored_key, district)

    _cache[stored_key] = result
    _save_cache()
    return result