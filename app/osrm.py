"""Полилинии маршрутов по дорогам (OSRM Public API).

Используется только для ОТРИСОВКИ маршрута на карте; планирование остаётся на
haversine. При недоступности OSRM возвращаем None — фронт рисует прямую линию.

Координаты в контракте — [lat, lng]; OSRM ожидает lon,lat, поэтому меняем местами.
"""

import requests

from app.config import get_settings

# Кэш: порядок точек -> полилиния [[lat, lng], ...]. Ограничим размер, чтобы
# сессия не росла бесконечно; план для одного инженера = один ключ.
_cache: dict[tuple, list[list[float]] | None] = {}
_MAX_CACHE = 512


def route_polyline(points: list[tuple[float, float]]) -> list[list[float]] | None:
    """Odrive-полилиния офис -> стопы (в заданном порядке). Points = (lat, lng)."""
    if not points or len(points) < 2:
        return None
    s = get_settings()
    if not s.osrm_enabled:
        return None

    key = tuple((round(lat, 6), round(lng, 6)) for lat, lng in points)
    if key in _cache:
        return _cache[key]

    coords = ";".join(f"{lng},{lat}" for lat, lng in key)
    result: list[list[float]] | None = None
    try:
        r = requests.get(
            s.osrm_url + "/route/v1/driving/" + coords,
            params={"overview": "full", "geometries": "geojson"},
            timeout=s.osrm_timeout,
        )
        if r.ok:
            data = r.json()
            if data.get("code") == "Ok" and data.get("routes"):
                geojson = data["routes"][0]["geometry"]["coordinates"]  # [[lon, lat], ...]
                result = [[lat, lng] for lng, lat in geojson]
    except Exception:
        result = None

    if len(_cache) >= _MAX_CACHE:
        _cache.clear()
    _cache[key] = result
    return result