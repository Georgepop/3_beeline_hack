"""Диспетчер: данные + солвер по активной конфигурации + сравнение.

Данные берутся из app/repository.py — единственного доступа к хранилищу (БД).
mode: baseline_fifo | improved | benchmark_ortools  →  какой решатель запускаем
      (реестр app/solvers). В сравнении оставляем контрольное распределение
      (справочно) и базовый FIFO.
"""

import time

from app import repository
from app import session
from app.config import get_settings
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
    """Заявки и инженеры региона из БД.

    active=False отдаёт и те заявки, что не планируются (статус «выполнена»
    и т.п.) — они нужны карте и спискам, но не плану.
    """
    return repository.requests_for(region, active=active), repository.engineers_for(region)


def solve(region: str, mode: str, dist: str) -> PlanResponse:
    # Кэш: переключение между режимами не должно каждый раз считать заново
    # (для OR-Tools это секунды ради того же ответа). Ключ — содержимое данных,
    # поэтому любое изменение набора заявок инвалидирует кеш само, без ручных
    # вызовов bump(); смотреть _REV в планировщике не нужно.
    # Данные читаются ДО проверки кеша: без них не из чего взять отпечаток.
    # Это чтение распарсенного региона (десятки КБ), на порядки дешевле расчёта.
    reqs, engs = dataset(region)
    cache_key = session.key(region, mode, dist, session.fingerprint(reqs, engs))
    cached = session.get(cache_key)
    if cached is not None:
        return cached

    started = time.perf_counter()
    plan = get_solver(mode)(region, dist, reqs, engs)

    control = None
    try:
        control = repository.control_metrics(region)
    except Exception:
        control = None
    plan.date = repository.plan_date(region) or plan.date

    plan.comparison = Comparison(control=control)
    plan.comparison = _with_baseline(plan, reqs, engs, dist, mode)
    from app import summary as summary_mod

    plan.summary = summary_mod.build_summary(plan, reqs, engs, dist)
    # Засекаем только солвер: контрольные метрики и сводка считаются и у
    # дешёвых планов, и порог записи на диск должен смотреть на их цену.
    session.store(cache_key, plan, elapsed=time.perf_counter() - started)
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
    # Отдельного bump() больше нет: ниже набор заявок меняется, поэтому у
    # нового плана будет другой отпечаток и он не совпадёт ни с одним
    # сохранённым — старый план останется в кеше нетронутым и сам вытеснится
    # по лимиту записей, а не сбросом всего кеша.

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
    # Ключ — по новому набору (с учётом события), поэтому /api/plan/geometry
    # после перепланирования отдаст геометрию именно для этого плана.
    session.store(session.key(region, mode, dist, session.fingerprint(reqs, engs)), after)
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

    office = repository.office_for(region)
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