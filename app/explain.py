"""Объяснение решения по одной заявке (ТЗ 2.1.7, шаг 4 демонстрации).

Заказчик просил не «портянку по каждой заявке» (цитата из созвона: читать её
не будут), а обоснование там, где решение неочевидно. Поэтому объяснение
по требованию: открыл заявку — получил разбор, и всё, что в нём написано,
посчитано тем же кодом, что и сам план.

Назначенная заявка: кто выбран, какие ограничения проверены, где стоит в
маршруте и кто ещё мог бы её взять (с добавочным пробегом) — контрафакт
считается вставкой в текущие маршруты, а не выдумывается.
Неназначенная: код и текст причины из reasons + подсказка, что помогло бы.
"""

from app import distance as ds
from app import planner
from app.schemas import Engineer, Request, RequestExplanation
from app.solvers import core, reasons

MAX_ALTERNATIVES = 3
MAX_REJECTED = 3


def _current_routes(plan, by_id: dict[str, Request], engineers: list[Engineer]) -> dict[str, list[Request]]:
    """Маршруты из плана, но в терминах исходных заявок — чтобы заново пересчитать."""
    stops = {e.id: e.stops for e in plan.engineers}
    return {e.id: [by_id[st.request_id] for st in stops.get(e.id, []) if st.request_id in by_id]
            for e in engineers}


def _try_insert(eng: Engineer, route: list[Request], req: Request,
                cur_km: float, dist: str) -> tuple[float, int] | None:
    """Минимальный добавочный пробег при вставке заявки в маршрут; None — не влезает."""
    best = None
    for pos in range(len(route) + 1):
        sr = core.schedule_route(eng, route[:pos] + [req] + route[pos:], dist)
        if sr is None:
            continue
        added = sr["km"] - cur_km
        if best is None or (added, pos) < best:
            best = (added, pos)
    return best


def _km(value: float) -> str:
    return f"{value:+.1f} км".replace("+", "+")


def explain(region: str, request_id: str, plan) -> RequestExplanation | None:
    """Разбор по заявке для уже посчитанного плана."""
    reqs, engs = planner.dataset(region)
    by_id = {r.id: r for r in reqs}
    req = by_id.get(request_id)
    if req is None:
        return None
    out = RequestExplanation(request_id=req.id, address=req.address)
    routes = _current_routes(plan, by_id, engs)
    by_eng = {e.id: e for e in engs}

    owner = next((e for e in plan.engineers
                  if any(st.request_id == request_id for st in e.stops)), None)
    if owner is None:
        return _explain_unassigned(out, req, engs, routes, plan.dist)

    stop = next(st for st in owner.stops if st.request_id == request_id)
    stop_no = stop.step
    route_len = len(owner.stops)
    out.assigned = True
    out.engineer = owner.name
    out.headline = (f"Назначена {owner.name} ({owner.transport_label or owner.transport}), "
                    f"заявка {stop_no}-я в маршруте из {route_len}. "
                    f"Начало работ {stop.start_work}, окончание {stop.finish}.")

    arrival = ds.parse_hhmm(stop.arrival)
    wait = ds.parse_hhmm(stop.start_work) - arrival
    need = "требований нет" if not req.required_transport else f"требуется {_label(req.required_transport)}"
    out.facts = [
        f"Навык «{req.skill_label}» входит в навыки исполнителя.",
        f"Транспорт: у исполнителя {owner.transport_label or owner.transport}, {need}.",
        f"Окно заявки {req.window_start}–{req.window_end}: прибытие {stop.arrival}, "
        f"начало работ {stop.start_work} (ожидание окна {wait} мин), финиш {stop.finish}, "
        f"норматив {req.duration_min} мин.",
        f"Смена {owner.shift_start}–{owner.shift_end}: работы заканчиваются за "
        f"{ds.parse_hhmm(owner.shift_end) - ds.parse_hhmm(stop.finish)} мин до конца смены.",
        f"Маршрут исполнителя: {route_len} заявок, пробег {owner.km} км, "
        f"занято {owner.minutes} мин.",
        "Срочные заявки обрабатываются первыми, остальные — по времени начала окна; "
        "затем каждый маршрут уточняется 2-opt, который сокращает пробег, не нарушая окон.",
    ]

    # Контрафакт: кто ещё из подходящих мог бы взять заявку в свой текущий маршрут.
    dist = plan.dist
    cur_km = {}
    for e in engs:
        sr = core.schedule_route(e, routes[e.id], dist)
        cur_km[e.id] = sr["km"] if sr else 0.0
    feasible: list[tuple[float, Engineer]] = []
    rejected: list[tuple[str, Engineer]] = []
    for e in engs:
        if e.id == owner.id:
            continue
        if not reasons.can_serve(req, e):
            rejected.append((_why_not_served(req, e), e))
            continue
        ins = _try_insert(e, routes[e.id], req, cur_km[e.id], dist)
        if ins is None:
            rejected.append((f"в текущий маршрут не помещается по времени "
                             f"(окно {req.window_start}–{req.window_end}, смена до {e.shift_end})", e))
            continue
        feasible.append((ins[0], e))
    feasible.sort(key=lambda x: x[0])
    out.alternatives = [{"engineer": e.name, "transport": e.transport_label or e.transport,
                         "added_km": round(added, 1)} for added, e in feasible[:MAX_ALTERNATIVES]]
    out.rejected = [{"engineer": e.name, "why": why} for why, e in rejected[:MAX_REJECTED]]

    if feasible:
        best_km, best_eng = feasible[0]
        out.facts.append(
            f"Из {len(feasible) + len(rejected)} других исполнителей заявку в свой маршрут "
            f"смогли бы добавить ещё {len(feasible)}: лучший вариант — {best_eng.name}, "
            f"{_km(best_km)} к его пробегу. Выбран вариант без лишнего пробега и с лучшей "
            f"загрузкой смены.")
    else:
        out.facts.append(
            f"Из {len(rejected)} других исполнителей никто не смог бы добавить заявку "
            f"в свой текущий маршрут — это единственное назначение, которое не ломает "
            f"обязательные ограничения.")
    if rejected:
        # Группой, а не списком: одиннадцать имён подряд — это «портянка», которой
        # заказчик просил избегать. Подробности остаются в rejected.
        groups: dict[str, int] = {}
        for why, _ in rejected:
            key = why.split(" (окно")[0] if why.startswith("в текущий") else why
            groups[key] = groups.get(key, 0) + 1
        out.facts.append("Не подошли (" + ", ".join(f"{v} — {k}" for k, v in groups.items()) + ").")
    return out


def _label(code: str) -> str:
    from app.regions import TRANSPORT
    return TRANSPORT.get(code, {}).get("label", code)


def _why_not_served(req: Request, eng: Engineer) -> str:
    if req.skill not in eng.skills:
        return f"нет навыка «{req.skill_label}»"
    return f"транспорт {eng.transport_label or eng.transport} не подходит"


def _explain_unassigned(out: RequestExplanation, req: Request, engs: list[Engineer],
                        routes: dict[str, list[Request]], dist: str) -> RequestExplanation:
    ctx = reasons.Context(busy=routes, dist=dist)
    info = reasons.classify(req, engs, ctx)
    out.assigned = False
    out.reason_code = info.reason_code
    out.reason = info.reason
    out.detail = info.reason_detail
    out.hint = reasons.hint(req, engs, info.reason_code, ctx)
    out.headline = f"Не назначена: {info.reason.lower()}"
    out.facts = [
        f"Окно заявки {req.window_start}–{req.window_end}, норматив {req.duration_min} мин, "
        f"приоритет «{'срочная' if req.priority == 'urgent' else 'обычная'}».",
        f"Проверено исполнителей: {len(engs)} в регионе, "
        f"подходящих по навыку и транспорту: {len(reasons.eligible(req, engs))}.",
    ]
    return out
