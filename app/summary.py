"""Сводка по плану: почему получился именно такой результат (ТЗ 2.4.2).

Заказчик просил объяснять результат «понятным диспетчеру языком» и отдельно —
кратко объяснить, какие ограничения повлияли на выбранный маршрут. Диспетчеру
нужны не все числа плана, а ответ на два вопроса: сколько работы смена в
принципе могла взять и что именно помешало дойти до этого числа.

Всё считается из уже принятого плана, тексты собираются из тех же величин,
поэтому сводка не может разойтись с самим планом.
"""

from app.schemas import PlanResponse, PlanSummary
from app.solvers import core

MAX_NOTE_FACTORS = 4


def _plural(n: int, one: str, few: str, many: str) -> str:
    """Согласование существительного с числом — иначе «1 заявок» в тексте для диспетчера."""
    n100, n10 = n % 100, n % 10
    if 11 <= n100 <= 14:
        return many
    if n10 == 1:
        return one
    if 2 <= n10 <= 4:
        return few
    return many


def _vplural(n: int, one: str, few: str, many: str) -> str:
    """Согласование сказуемого: «1 заявка не поместилась», «2 заявки не поместились»."""
    return _plural(n, one, few, many)


def build_summary(plan: PlanResponse, requests: list, engineers: list, dist: str) -> PlanSummary:
    """Собирает сводку: потолок смены, доля выполнения и то, что встало на пути."""
    m = plan.metrics
    ceiling = core.shift_ceiling(requests, engineers, dist)
    gap = ceiling - m.assigned_count
    out = PlanSummary(
        assigned=m.assigned_count,
        total=m.total_requests,
        ceiling=ceiling,
        engineers_used=m.engineers_used,
        total_km=m.total_km,
        total_minutes=m.total_minutes,
    )
    if ceiling:
        out.headline = (f"Выполнено {m.assigned_count} из {m.total_requests} заявок "
                        f"({m.engineers_used} исполнителей, {m.total_km} км). "
                        f"Предел смены по жёстким окнам — {ceiling} заявок, "
                        f"то есть план взял {round(100 * m.assigned_count / ceiling)}% от возможного.")
    else:
        out.headline = (f"Выполнено {m.assigned_count} из {m.total_requests} заявок "
                        f"({m.engineers_used} исполнителей, {m.total_km} км).")

    counts: dict[str, int] = {}
    for u in plan.unassigned:
        counts[u.reason_code] = counts.get(u.reason_code, 0) + 1
    out.reason_counts = counts

    factors: list[str] = []
    if counts.get("no_capacity"):
        n = counts["no_capacity"]
        factors.append(f"{n} {_plural(n, 'заявка', 'заявки', 'заявок')} "
                       f"{_vplural(n, 'не поместилась', 'не поместились', 'не поместились')} "
                       f"по времени: подходящие исполнители уже загружены")
    if counts.get("outside_shift"):
        n = counts["outside_shift"]
        factors.append(f"{n} {_plural(n, 'заявка', 'заявки', 'заявок')} "
                       f"{_vplural(n, 'не помещается', 'не помещаются', 'не помещаются')} в смену "
                       f"даже как единственная работа (окно или норматив)")
    if counts.get("no_transport"):
        n = counts["no_transport"]
        factors.append(f"{n} {_plural(n, 'требует', 'требуют', 'требуют')} транспорт, которого нет в регионе")
    if counts.get("no_skill"):
        n = counts["no_skill"]
        factors.append(f"{n} {_plural(n, 'требует', 'требуют', 'требуют')} навыка, которого нет "
                       f"среди исполнителей")
    if counts.get("no_coords"):
        n = counts["no_coords"]
        factors.append(f"{n} {_plural(n, 'заявка', 'заявки', 'заявок')} без координат — адрес не геокодирован")
    out.factors = factors[:MAX_NOTE_FACTORS]

    if gap > 0 and not counts.get("no_capacity"):
        out.text = (f"До предела смены не хватило {gap} {_plural(gap, 'заявки', 'заявок', 'заявок')}, "
                    f"хотя свободное время и навыки были: упирается в жёсткие окна и длительность работ.")
    elif gap > 0:
        out.text = (f"Не добралось {gap} {_plural(gap, 'заявки', 'заявок', 'заявок')} до предела смены. "
                    f"Основное ограничение — время в пути и транспорт бригад, а не число исполнителей.")
    elif m.unassigned_count == 0:
        out.text = "Все заявки выполнены в пределах ограничений."
    else:
        out.text = "Все выполнимые заявки распределены; невыполненные требуют вмешательства диспетчера."
    return out
