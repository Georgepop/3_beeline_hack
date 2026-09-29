"""Дорожная геометрия и матрица расстояний (OSRM Public API).

Два независимых потребителя:
- routes_polyline() — полилинии маршрутов для отрисовки на карте. Вызывается из
  GET /api/plan/geometry уже после того, как план посчитан, поэтому сетевая
  задержка не стоит в критическом пути;
- table_matrix_km() — матрица дорожных расстояний. Её использует только режим
  «Контур» (app/solvers/ortools_office.py), которому нужны реальные длины дуг,
  а не оценка по прямой. Остальные режимы считают по haversine и сюда не ходят.

Из планировщика роут тянуться перестал намеренно: 12 запросов по одному на
инженера, каждый с новым TLS-рукопожатием, давали 4.4 с на регион.

Что здесь ускорено и почему:
- Session с keep-alive вместо requests.get на каждый вызов: 12 маршрутов
  4.4 с -> 1.13 с (замерено на router.project-osrm.org);
- пакетная загрузка в ThreadPoolExecutor: маршруты одного плана независимы;
- дисковый кэш data/osrm_cache.json: после первого показа повторные запуски
  и перезапуски сервера не ходят в сеть вообще;
- osrm_timeout 4 с: публичный демо-сервер может быть недоступен, но для
  отрисовки это уже не блокирует расчёт (фронт рисует прямую линию), а для
  матрицы «Контур» означает откат на haversine.

Координаты в контракте — [lat, lng]; OSRM ожидает lon,lat, поэтому меняем местами.
При любой ошибке routes_polyline возвращает None — фронт рисует прямую линию.
table_matrix_km при ошибке возвращает None целиком, а не частично: половина
матрицы от OSRM и половина от haversine — это не матрица, а ерунда, которую потом
невозможно объяснить.
"""

import json
from concurrent.futures import ThreadPoolExecutor
from itertools import product
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

# Префикс ключа матрицы. Ключи маршрутов — просто координаты через «;», поэтому
# «tbl:» однозначно отделяет матрицы от полилиний в общем кэше.
_TABLE_PREFIX = "tbl:"

# Недостижимая пара внутри успешного ответа OSRM (значение null). Число конечное,
# потому что OR-Tools принимает только целые: 10 000 км заведомо больше любой дуги
# в городе, поэтому такой узел решатель просто не выберет.
_UNREACHABLE_KM = 10_000.0



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


def _fetch_table(sources: list[int], dests: list[int], all_key: str) -> list[list[float]] | None:
    """Блок матрицы OSRM table — расстояния от sources до dests в км.

    Ошибка запроса -> None (вызывающий откатывается на haversine). Недостижимая
    пара внутри успешного ответа (значение null) -> _UNREACHABLE_KM: это не
    поломка запроса, а «сюда ехать нельзя», и решатель сам её обойдёт.
    """
    s = get_settings()
    try:
        r = _get_session().get(
            f"{s.osrm_url}/table/v1/driving/{all_key}",
            params={
                "annotations": "distance",
                "sources": ";".join(str(i) for i in sources),
                "destinations": ";".join(str(i) for i in dests),
            },
            timeout=s.osrm_timeout,
        )
        if r.ok:
            data = r.json()
            if data.get("code") == "Ok":
                rows = data.get("distances") or []
                return [[_UNREACHABLE_KM if v is None else round(v / 1000.0, 3) for v in row]
                        for row in rows]
    except Exception:
        return None
    return None


def _blocks(n: int, size: int) -> list[list[int]]:
    return [list(range(i, min(i + size, n))) for i in range(0, n, size)]


def table_matrix_km(points: list[tuple[float, float]]) -> list[list[float]] | None:
    """Матрица дорожных расстояний между точками, км. None — сеть недоступна.

    Кэшируется целиком, одним элементом на весь набор: матрица нужна всегда вся и
    всегда для одного региона, дробить её в кэше незачем. Отказ сети НЕ
    кэшируется — на следующий запрос сервер может ответить, и «Контур» посчитает
    по-честному на дорогах, а не застрянет на haversine из-за одного сбоя.
    """
    _load_cache()
    s = get_settings()
    n = len(points)
    if not s.osrm_enabled or n == 0:
        return None

    all_key = _key(points)
    ck = _TABLE_PREFIX + all_key
    if ck in _cache:
        return _cache[ck]

    blocks = _blocks(n, max(1, s.osrm_table_max_points))
    pairs = list(product(blocks, blocks))
    with ThreadPoolExecutor(max_workers=_MAX_WORKERS) as ex:
        fetched = list(ex.map(lambda p: _fetch_table(p[0], p[1], all_key), pairs))
    if any(part is None for part in fetched):
        return None

    matrix = [[_UNREACHABLE_KM] * n for _ in range(n)]
    for (srcs, dsts), part in zip(pairs, fetched):
        for i, si in enumerate(srcs):
            for j, dj in enumerate(dsts):
                matrix[si][dj] = part[i][j]
    for i in range(n):
        matrix[i][i] = 0.0

    _cache[ck] = matrix
    _trim()
    _save_cache()
    return matrix


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
