"""Память процесса: последний посчитанный план и ревизия набора заявок.

План нигде не хранится — прототип пересчитывает его на каждый запрос /api/plan.
Но объяснение по заявке обязано ссылаться на тот же план, что сейчас на экране,
иначе диспетчер увидит «почему так» для уже неактуального маршрута. Поэтому
держим последний результат вместе с ревизией набора заявок: как только заявки
изменились (сценарий перепланирования, правка вручную), ревизия растёт и
объяснение пересчитывается, а не отдаёт устаревшее.

Ключ — (регион, режим, режим расстояний): у каждой комбинации свой план.
"""

from app.schemas import PlanResponse

_REV = 0
_PLANS: dict[tuple[str, str, str], tuple[int, PlanResponse]] = {}


def bump() -> int:
    """Набор заявок изменился — все сохранённые планы считаем устаревшими."""
    global _REV
    _REV += 1
    return _REV


def revision() -> int:
    return _REV


def store(key: tuple[str, str, str], plan: PlanResponse) -> None:
    _PLANS[key] = (_REV, plan)


def get(key: tuple[str, str, str]) -> PlanResponse | None:
    """План, соответствующий текущему набору заявок, или None — тогда считаем заново."""
    hit = _PLANS.get(key)
    return hit[1] if hit and hit[0] == _REV else None


def clear() -> None:
    global _REV
    _REV = 0
    _PLANS.clear()
