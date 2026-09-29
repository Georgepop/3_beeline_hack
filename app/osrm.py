"""Полилинии маршрутов по дорогам (OSRM Public API).

Используется ТОЛЬКО для отрисовки на карте; расписание всегда на haversine и с
этим модулем не связано. Обращается сюда GET /api/plan/geometry — уже после того
как план посчитан, поэтому сетевая задержка не стоит в критическом пути.

Отсюда роут в планировщике был удалён намеренно: 12 запросов по одному на
инженера, каждый с новым TLS-рукопожатием, давали 4.4 с на регион.

Что здесь ускорено и почему:
- Session с keep-alive вместо requests.get на каждый вызов: 12 маршрутов
  4.4 с -> 1.13 с (замерено на router.project-osrm.org);
- пакетная загрузка в ThreadPoolExecutor: маршруты одного плана независимы;
- дисковый кэш data/osrm_cache.json: после первого показа повторные запуски
  и перезапуски сервера не ходят в сеть вообще;
- osrm_timeout 4 с: публичный демо-сервер может быть недоступен, но это уже не
  блокирует расчёт плана, поэтому ждать дольше бессмысленно.

Координаты в контракте — [lat, lng]; OSRM ожидает lon,lat, поэтому меняем местами.
При любой ошибке возвращаем None — фронт рисует прямую линию.
"""

import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import requests

from app.config import get_settings

CACHE_PATH = Path("data/osrm_cache.json")

# Кэш в памяти: зеркало дискового, чтобы не читать файл на каждый полигон.
_cache: dict[str, list[list[float]] | None] = {}
_loaded = False

# Сессия на весь процесс: переиспользование TCP + TLS между запросами.
_session: requests.Session | None = None

_MAX_CACHE = 5000
_MAX_WORKERS = 4   # публичный демо-сервер штрафует за параллель; 4 — компромисс


def _get_session() -> requests.Session:
    global _session
    if _session is None:
        _session = requests.Session()
    return _session


def _load_cache() -> None:
    global _loaded, _cache
    if _loaded:
        return
    _loaded = True
    if CACHE_PATH.exists():
        try:
            _cache = json.loads(CACHE_PATH.read_text(encoding="utf-8"))
        except Exception:
            _cache = {}


def _save_cache() -> None:
    try:
        CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
        CACHE_PATH.write_text(json.dumps(_cache, ensure_ascii=False), encoding="utf-8")
    except Exception:
        pass  # кэш — ускорение, а не условие работы


def _key(points: list[tuple[float, float]]) -> str:
    """Строка координат для URL OSRM.

    Порядок именно lon,lat: в контракте точки приходят как (lat, lng), а OSRM
    ожидает долготу первой. Этот же ключ служит ключом кэша.
    """
    return ";".join(f"{lng},{lat}" for lat, lng in points)


def _fetch_one(key: str) -> list[list[float]] | None:
    """Один маршрут по дорогам. Любая ошибка -> None (фронт рисует прямую)."""
    s = get_settings()
    try:
        r = _get_session().get(
            f"{s.osrm_url}/route/v1/driving/{key}",
            params={"overview": "full", "geometries": "geojson"},
            timeout=s.osrm_timeout,
        )
        if r.ok:
            data = r.json()
            if data.get("code") == "Ok" and data.get("routes"):
                geojson = data["routes"][0]["geometry"]["coordinates"]  # [[lon, lat], ...]
                # 5 знаков ~ 1 м: для отрисовки хватает, а JSON втрое компактнее
                return [[round(lat, 5), round(lng, 5)] for lng, lat in geojson]
    except Exception:
        return None
    return None


def routes_polyline(paths: list[list[tuple[float, float]]]) -> list[list[list[float]] | None]:
    """Полилинии для набора маршрутов. Порядок ответа совпадает с порядком paths.

    Из кэша отдаётся сразу, недостающее грузится пачками. Единичный промах
    кэшируется как None, чтобы не опрашивать сервер по одному и тому же адресу
    при каждом переключении региона.
    """
    _load_cache()
    s = get_settings()
    results: list[list[list[float]] | None] = [None] * len(paths)
    if not s.osrm_enabled or not paths:
        return results

    keys = [_key(p) if len(p) >= 2 else "" for p in paths]
    missing: list[int] = []
    for i, k in enumerate(keys):
        if not k:
            continue
        if k in _cache:
            results[i] = _cache[k]
        else:
            missing.append(i)

    if missing:
        with ThreadPoolExecutor(max_workers=_MAX_WORKERS) as ex:
            fetched = list(ex.map(_fetch_one, [keys[i] for i in missing]))
        for i, val in zip(missing, fetched):
            results[i] = val
            _cache[keys[i]] = val
        _trim()
        _save_cache()

    return results


def route_polyline(points: list[tuple[float, float]]) -> list[list[float]] | None:
    """Одиночная полилиния — обёртка над пакетной загрузкой (совместимость)."""
    return routes_polyline([points])[0]


def _trim() -> None:
    if len(_cache) > _MAX_CACHE:
        # Порядок вставки сохраняется, поэтому режем хвост — самые старые записи.
        for k in list(_cache)[: len(_cache) - _MAX_CACHE]:
            del _cache[k]


def clear() -> None:
    global _loaded
    _cache.clear()
    _loaded = True
    if CACHE_PATH.exists():
        try:
            CACHE_PATH.unlink()
        except Exception:
            pass
