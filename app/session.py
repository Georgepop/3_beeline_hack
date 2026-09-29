"""Память процесса: последние посчитанные планы и ревизия набора заявок.

Здесь две разные вещи, и их важно не путать:

1. Кэш плана. Раньше /api/plan пересчитывался на каждый запрос, хотя набор данных
   не менялся: переключение туда-обратно между режимами стоило полного расчёта
   (для OR-Tools — 30 с) ради того же ответа. Теперь plan лежит здесь, и повторный
   запрос отдаётся сразу.

2. Привязка объяснения к экрану. /api/explain обязан считать по тому же плану,
   который сейчас показан, иначе диспетчер увидит «почему так» для уже
   неактуального маршрута. Поэтому кэш хранится вместе с ревизией набора
   заявок: как только заявки изменились (сценарий, правка, смена источника
   данных), ревизия растёт и всё пересчитывается.

Ключ — (регион, режим, режим расстояний, источник данных). Источник данных
включён намеренно: у csv и remote разные заявки, и общий ключ отдавал бы план,
собранный из чужих точек.

Планы в кэше — расписание без геометрии (route=None у всех инженеров): полилинии
OSRM не хранятся в памяти процесса, они лежат в data/osrm_cache.json и
приезжают отдельным запросом.
"""

from app.schemas import PlanResponse

_REV = 0
_PLANS: dict[tuple[str, str, str, str], tuple[int, PlanResponse]] = {}


def key(region: str, mode: str, dist: str, source: str = "") -> tuple[str, str, str, str]:
    """Ключ плана. source пустой для вызовов без настроек (тогда берём текущий)."""
    from app.config import get_settings

    return (region, mode, dist, source or get_settings().data_source)


def bump() -> int:
    """Набор заявок изменился — все сохранённые планы считаем устаревшими."""
    global _REV
    _REV += 1
    return _REV


def revision() -> int:
    return _REV


def store(key: tuple[str, str, str, str], plan: PlanResponse) -> None:
    _PLANS[key] = (_REV, plan)


def get(key: tuple[str, str, str, str]) -> PlanResponse | None:
    """План, соответствующий текущему набору заявок, или None — тогда считаем заново.

    Отдаём копию: вызывающий (planner) дописывает в план поля, и без копии он
    испортил бы объект в кэше для всех, кто придёт следом.
    """
    hit = _PLANS.get(key)
    if not hit or hit[0] != _REV:
        return None
    return hit[1].model_copy(deep=True)


def clear() -> None:
    global _REV
    _REV = 0
    _PLANS.clear()
