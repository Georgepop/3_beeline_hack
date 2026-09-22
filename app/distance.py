"""Геометрические и временные вычисления.

distance_km(): haversine (прямая) с дорожным коэффициентом; позже добавится OSRM.
Время: минуты от начала суток <-> "HH:MM".
"""

import math
from datetime import datetime

from app.config import get_settings
from app.schemas import LatLng


def _haversine_km(a: LatLng, b: LatLng) -> float:
    r = 6371.0
    p1, p2 = math.radians(a.lat), math.radians(b.lat)
    dp = math.radians(b.lat - a.lat)
    dl = math.radians(b.lng - a.lng)
    h = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(h))


def distance_km(a: LatLng, b: LatLng) -> float:
    """Прямое расстояние * дорожный коэффициент (допущение) — поправка на непрямую езду."""
    return _haversine_km(a, b) * get_settings().road_factor


def travel_minutes_km(km: float, transport: str = "auto") -> int:
    speed = get_settings().speed_kmh.get(transport, 5.0)
    return int(round(km / speed * 60))


def parse_hhmm(hhmm: str) -> int:
    hh, mm = hhmm.split(":")[:2]
    return int(hh) * 60 + int(mm)


def to_hhmm(minutes: int) -> str:
    minutes = max(0, int(minutes))
    return f"{minutes // 60:02d}:{minutes % 60:02d}"


def now_hhmm() -> str:
    return datetime.now().strftime("%H:%M")