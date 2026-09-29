"""Диспетчер: данные + солвер по активной конфигурации + сравнение.

data_source: mock | csv | remote | db  →  откуда берём заявки/инженеров/офис.
mode: baseline_fifo | improved | benchmark_ortools  →  какой решатель запускаем
      (реестр app/solvers; сейчас реализован improved; остальные — задел).
В сравнении оставляем контрольное распределение (справочно).
"""

from app.config import get_settings
from app import mock
from app.regions import REGIONS, SKILLS, norm_min
from app.schemas import (
    Comparison,
    Engineer,
    PlanResponse,
    Request,
    ScenarioEvent,
    ScenarioResult,
)
from app.solvers import available as solver_available
from app.solvers import get as get_solver


def dataset(region: str, active: bool = True) -> tuple[list[Request], list[Engineer]]:
    ds = get_settings().data_source
    if ds == "remote":
        from app import remote_source
        reqs = remote_source.active_requests(region) if active else remote_source.requests_for(region)
        return reqs, remote_source.engineers_for(region)
    if ds in ("csv", "db"):
        from app.data_source import engineers_for, requests_for
        return requests_for(region), engineers_for(region)
    return mock.build_requests(region), mock.build_engineers(region)


def solve(region: str, mode: str, dist: str) -> PlanResponse:
    reqs, engs = dataset(region)
    plan = get_solver(mode)(region, dist, reqs, engs)

    control = None
    ds = get_settings().data_source
    if ds == "remote":
        from app import remote_source
        control = remote_source.control_metrics(region)
        plan.date = remote_source.plan_date(region) or plan.date
    elif ds in ("csv", "db"):
        try:
            from app.data_source import control_metrics
            control = control_metrics(region)
        except Exception:
            control = None

    plan.comparison = Comparison(control=control)
    return plan


def replay(region: str, event: ScenarioEvent) -> ScenarioResult:
    reqs, engs = dataset(region)
    mode, dist = _active_mode()
    before = get_solver(mode)(region, dist, reqs, engs)

    if event.type == "urgent":
        reqs = reqs + [_urgent_request(region)]
    elif event.type == "cancel":
        if not event.request_id:
            raise ValueError("cancel требует request_id")
        reqs = [r for r in reqs if r.id != event.request_id]
    elif event.type == "unavailable":
        if not event.engineer_id:
            raise ValueError("unavailable требует engineer_id")
        eng = next((e for e in engs if e.id == event.engineer_id), None)
        if not eng:
            raise ValueError("Инженер не найден")
        reqs = [r for r in reqs if r.id not in {st.request_id for st in eng.stops}]
        engs = [e for e in engs if e.id != event.engineer_id]
    else:
        raise ValueError("Неизвестный тип события")

    after = get_solver(mode)(region, dist, reqs, engs)
    return ScenarioResult(event=_event_label(event), plan=after, diff=_compare_plans(before, after))


def _active_mode() -> tuple[str, str]:
    """Сценарий пересчитывается в том же режиме, что и план (иначе демо покажет не тот солвер)."""
    s = get_settings()
    mode = s.solver_mode
    if not solver_available(mode):
        mode = "improved"
    return mode, s.dist_mode


def _compare_plans(before: PlanResponse, after: PlanResponse) -> list[dict]:
    def mapping(p: PlanResponse) -> dict:
        m = {}
        for e in p.engineers:
            for st in e.stops:
                m[st.request_id] = e.name
        return m

    b, a = mapping(before), mapping(after)
    diff: list[dict] = []
    for rid in sorted(set(b) | set(a)):
        if rid in a and rid not in b:
            diff.append({"request_id": rid, "kind": "assigned", "to": a[rid]})
        elif rid in b and rid not in a:
            diff.append({"request_id": rid, "kind": "unassigned", "from": b[rid]})
        elif b[rid] != a[rid]:
            diff.append({"request_id": rid, "kind": "reassigned", "from": b[rid], "to": a[rid]})
    return diff


def _event_label(event: ScenarioEvent) -> str:
    if event.type == "urgent":
        return "Поступила срочная аварийная заявка"
    if event.type == "cancel":
        return f"Отменена заявка {event.request_id}"
    return "Инженер недоступен, заявки перераспределены"


def _urgent_request(region: str) -> Request:
    import random

    from app.data_source import office_for

    office = office_for(region)
    rng = random.Random(region + ":urgent")
    return Request(
        id=f"URG-{region[:3].upper()}",
        bk_type="Глобальная проблема",
        hd_type="Авария",
        skill="avariynye",
        skill_label=SKILLS["avariynye"]["label"],
        window_start="08:00",
        window_end="23:59",
        district="—",
        address="Аварийная заявка (сценарий)",
        priority="urgent",
        duration_min=norm_min("Глобальная проблема"),
        lat=round(office.lat + (rng.random() - 0.5) * 0.04, 6),
        lng=round(office.lng + (rng.random() - 0.5) * 0.04, 6),
    )