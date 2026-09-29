"""Кэш плана: в памяти процесса и на диске.

Зачем он вообще нужен. /api/plan пересчитывался на каждый запрос, хотя набор
данных не менялся: переключение туда-обратно между режимами стоило полного
расчёта (для OR-Tools это было 30 с) ради того же ответа. Теперь план лежит в
кэше и повторный запрос отдаётся сразу.

Ключ кеша — содержимое данных, а не счётчик событий:

    region | mode | dist | source | отпечаток(заявки, инженеры)

Раньше для инвалидации стоял счётчик ревизий, и любое изменение данных должно
было вызвать session.bump(). На практике его звали только два места — смена
источника данных и сценарий перепланирования, — а прямая правка CSV не звала
ничего, и кэш молча отдавал устаревший план. Отпечаток закрывает эту дыру по
построению: изменилась хоть одна заявка (координаты, окно, длительность, навык)
или хоть один инженер (смена, транспорт, скорость) — отпечаток другой, кеш
промахнулся. Счётчика больше нет, и забыть что-то вызвать уже негде.

Второе следствие — и ради него диск: одни и те же данные дают один и тот же
отпечаток, поэтому кеш переживает перезапуск сервера. Со счётчиком это было бы
невозможно: он обнуляется при старте, и запись, сделанная до рестарта, либо
отвергалась, либо, наоборот, выдавалась после изменения данных, случившегося
пока сервер был выключен.

Что кешируется. На диск попадает только то, ради чего кеш и нужен: план,
после расчёта которого прошла секунда и больше (то есть OR-Tools). improved
считается за 0.05 с и писать его на диск — только лишний ввод-вывод.

Планы в кэше — расписание без геометрии (route=None у всех инженеров):
полилинии OSRM не хранятся здесь, они лежат в data/osrm_cache.json и приезжают
отдельным запросом.

Сбой кеша не должен ломать планирование, поэтому всё чтение и запись диска
завёрнуты в try/except: битый или несовместимый файл просто игнорируется.
"""

import hashlib
import json
import time
from pathlib import Path

from app.schemas import PlanResponse

CACHE_PATH = Path("data/plan_cache.json")

# Формат кеша меняем вместе с кодом плана: иначе после правки схемы сервер
# отдаст из файла план, собранный прошлой версией кода.
_FORMAT_VERSION = 1

# Планов немного (регион × режим × режим расстояний × источник), но смена
# данных заставит старые записи вытесняться, поэтому держим хвост.
_MAX_ENTRIES = 50

# Порог, с которого план стоит сохранить на диск. Ниже — расчёт настолько
# дешёвый, что повторный запрос дешевле чтения файла.
_PERSIST_AFTER_SEC = 1.0

_PLANS: dict[str, PlanResponse] = {}
_loaded = False


def fingerprint(requests, engineers) -> str:
    """Отпечаток содержимого данных: одинаковые данные — одинаковый отпечаток.

    Сортировка по id нужна, чтобы порядок заявок в файле не влиял на результат:
    иначе перестановка строк в CSV дала бы ложный промах кеша.
    """
    h = hashlib.sha1()
    for r in sorted(requests, key=lambda x: x.id):
        h.update(r.model_dump_json().encode("utf-8"))
        h.update(b"\x00")
    h.update(b"|engineers|")
    for e in sorted(engineers, key=lambda x: x.id):
        h.update(e.model_dump_json().encode("utf-8"))
        h.update(b"\x00")
    return h.hexdigest()[:16]


def key(region: str, mode: str, dist: str, fp: str = "", source: str = "") -> str:
    """Ключ плана. fp пустой для вызовов без данных (тогда отпечаток пустой)."""
    from app.config import get_settings

    return "|".join((region, mode, dist, source or get_settings().data_source, fp))


def _load() -> None:
    global _loaded
    if _loaded:
        return
    _loaded = True
    if not CACHE_PATH.exists():
        return
    try:
        raw = json.loads(CACHE_PATH.read_text(encoding="utf-8"))
        if not isinstance(raw, dict) or raw.get("version") != _FORMAT_VERSION:
            return
        for k, v in (raw.get("plans") or {}).items():
            try:
                _PLANS[k] = PlanResponse.model_validate(v)
            except Exception:
                continue  # битая запись не должна ломать остальной кеш
    except Exception:
        _PLANS.clear()


def _save() -> None:
    if len(_PLANS) > _MAX_ENTRIES:
        # Порядок вставки сохраняется, поэтому режем хвост — самые старые записи.
        for k in list(_PLANS)[: len(_PLANS) - _MAX_ENTRIES]:
            del _PLANS[k]
    try:
        CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
        payload = {"version": _FORMAT_VERSION, "plans": {k: v.model_dump(mode="json") for k, v in _PLANS.items()}}
        CACHE_PATH.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    except Exception:
        pass  # кеш — ускорение, а не условие работы


def get(key: str) -> PlanResponse | None:
    """План для этих данных или None — тогда считаем заново.

    Отдаём копию: вызывающий (planner) дописывает в план поля, и без копии он
    испортил бы объект в кэше для всех, кто придёт следом.
    """
    _load()
    hit = _PLANS.get(key)
    return hit.model_copy(deep=True) if hit is not None else None


def store(key: str, plan: PlanResponse, elapsed: float = 0.0) -> None:
    """Сохранить план. На диск — только если расчёт занял дольше порога."""
    _load()
    _PLANS[key] = plan.model_copy(deep=True)
    if elapsed >= _PERSIST_AFTER_SEC:
        _save()


def clear() -> None:
    _PLANS.clear()
    if CACHE_PATH.exists():
        try:
            CACHE_PATH.unlink()
        except Exception:
            pass


def stats() -> dict:
    """Диагностика кеша: сколько планов в памяти и на диске."""
    _load()
    on_disk = 0
    if CACHE_PATH.exists():
        try:
            raw = json.loads(CACHE_PATH.read_text(encoding="utf-8"))
            on_disk = len(raw.get("plans") or {}) if raw.get("version") == _FORMAT_VERSION else 0
        except Exception:
            on_disk = 0
    return {
        "in_memory": len(_PLANS),
        "on_disk": on_disk,
        "path": str(CACHE_PATH),
    }
