"""Диспетчер: данные + солвер по активной конфигурации + сравнение.

data_source: mock | csv | remote | db  →  откуда берём заявки/инженеров/офис.
mode: baseline_fifo | improved | benchmark_ortools  →  какой решатель запускаем
      (реестр app/solvers; сейчас реализован improved; остальные — задел).
В сравнении оставляем контрольное распределение (справочно).
"""

from app.config import get_settings
from app import mock
from app import session
from app.regions import REGIONS, SKILLS, norm_min
from app.schemas import (
    Comparison,
    Engineer,
    PlanMetrics,
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
    # Кэш: переключение между режимами не должно каждый раз считать заново
    # (для OR-Tools это 30 с ради того же ответа). Ревизия сбрасывает кэш,
    # когда меняется набор заявок, а источник данных входит в сам ключ.
    cache_key = session.key(region, mode, dist)
    cached = session.get(cache_key)
    if cached is not None:
        return cached

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
    plan.comparison = _with_baseline(plan, reqs, engs, dist, mode)
    from app import summary as summary_mod

    plan.summary = summary_mod.build_summary(plan, reqs, engs, dist)
    session.store(cache_key, plan)
    return plan


def _with_baseline(plan: PlanResponse, reqs, engs, dist: str, mode: str) -> Comparison:
    """Сравнение с базовым FIFO — обязательный пункт ТЗ (оценка качества решения).

    FIFO считается всегда, когда активен не он сам: он заметно дешевле улучшенного,
    поэтому показывает выигрыш текущего режима на тех же данных. Обратный случай
    (активен FIFO) улучшенный солвер не запускаем — это демонстрация базы, а не
    сравнение; в note тогда честно сказано, что сравнение доступно на improved.
    """
    comp = plan.comparison
    comp.improved_mode = mode
    comp.baseline_mode = "baseline_fifo"
    if mode == "baseline_fifo":
        comp.baseline = plan.metrics
        comp.note = "Активен базовый FIFO. Переключите режим на «Улучшенный», чтобы увидеть выигрыш."
        return comp

    base = get_solver("baseline_fifo")(plan.region, dist, reqs, engs)
    comp.baseline = base.metrics
    comp.improved = plan.metrics
    d = plan.metrics.assigned_count - base.metrics.assigned_count
    comp.note = _comparison_note(base.metrics, plan.metrics, d)
    return comp


def _comparison_note(base: PlanMetrics, cur: PlanMetrics, d: int) -> str:
    """Фраза о выигрыше. Пробег сравниваем на заявку: суммарный растёт вместе с
    числом выполненных заявок и сам по себе ничего не говорит о качестве."""
    if d > 0:
        per_base = base.total_km / base.assigned_count if base.assigned_count else 0.0
        per_cur = cur.total_km / cur.assigned_count if cur.assigned_count else 0.0
        parts = [f"выполнено заявок на {d} больше"]
        if per_base and per_cur:
            delta = per_cur - per_base
            parts.append(f"пробег на заявку {per_base:.1f} → {per_cur:.1f} км "
                         f"({'+' if delta >= 0 else '−'}{abs(delta):.1f})")
        return "Улучшенный алгоритм: " + ", ".join(parts) + "."
    if d == 0:
        return ("Улучшенный алгоритм выполняет столько же заявок, сколько FIFO — "
                "выигрыш здесь в пробеге и загрузке, а не в числе выполненных заявок.")
    return (f"Улучшенный алгоритм выполняет на {abs(d)} заявок меньше, чем FIFO; "
            f"это отклонение требует разбора.")


def replay(region: str, event: ScenarioEvent) -> ScenarioResult:
    reqs, engs = dataset(region)
    mode, dist = _active_mode()
    before = get_solver(mode)(region, dist, reqs, engs)
    session.bump()  # набор заявок изменился — сохранённые планы больше не актуальны

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
    from app import summary as summary_mod

    after.summary = summary_mod.build_summary(after, reqs, engs, dist)
    session.store(session.key(region, mode, dist), after)
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