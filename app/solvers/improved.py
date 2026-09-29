"""Улучшенный план: срочные заявки первыми → insertion (лучший инженер + позиция
по добавочному пробегу) → локальный 2-opt по каждому маршруту (с проверкой окон)."""

from app.solvers import core, reasons
from app.schemas import Engineer, Request, UnassignedReason


def _ordered(reqs: list[Request]) -> list[Request]:
    return sorted(reqs, key=lambda r: (0 if r.priority == "urgent" else 1, core.ds.parse_hhmm(r.window_start)))


def _rescue_urgent(requests, engineers, eng, routes, cur_km, unassigned, dist) -> None:
    """Переселение срочной заявки за счёт обычной, если в текущем виде она не влезла.

    Сортировка «срочные первыми» даёт срочной заявке преимущество только на старте:
    позже она может не поместиться к уже занятому инженеру. Здесь для каждой
    неназначенной срочной заявки делается одна попытка — вытеснить из её маршрута
    ровно одну обычную заявку и поселить её другому инженеру. Попытка одна и
    только с обычной заявкой: иначе начинается каскад переселений и качество
    плана на равных заявках ухудшается.
    """
    by_id = {r.id: r for r in requests}
    for u in list(unassigned):
        req = by_id.get(u.request_id)
        if req is None or req.priority != "urgent":
            continue
        if _try_evict(req, engineers, routes, dist):
            unassigned.remove(u)


def _try_evict(req: Request, engineers, routes, dist) -> bool:
    """Одна попытка вставить срочную заявку, вытеснив из маршрута одну обычную.

    Возвращает True, если заявка встала на место (маршруты уже обновлены).
    Вытесняемая заявка ищется в порядке наименьшего добавленного пробега, который
    освобождает место, — так вытесняется наименее ценная работа.
    """
    moves: list[tuple[float, str, int, Request, str, int]] = []
    for e in engineers:
        cur = routes[e.id]
        for victim_pos, victim in enumerate(cur):
            if victim.priority == "urgent":
                continue  # срочную не вытесняем
            trimmed = cur[:victim_pos] + cur[victim_pos + 1:]
            if core.schedule_route(e, trimmed + [req], dist) is None:
                continue  # даже без жертвы срочная сюда не влезает
            # Куда переселить жертву: ищем инженера, которому она реально подходит.
            for other in engineers:
                if other.id == e.id or victim.skill not in other.skills:
                    continue
                if victim.required_transport and other.transport != victim.required_transport:
                    continue
                for opos in range(len(routes[other.id]) + 1):
                    trial = routes[other.id][:opos] + [victim] + routes[other.id][opos:]
                    if core.schedule_route(other, trial, dist) is None:
                        continue
                    km = core.schedule_route(e, trimmed + [req], dist)["km"] \
                        + core.schedule_route(other, trial, dist)["km"]
                    moves.append((km, e.id, victim_pos, victim, other.id, opos))
                    break  # для этой пары инженеров достаточно лучшей позиции
    if not moves:
        return False
    _, eid, vpos, victim, oid, opos = min(moves, key=lambda m: m[0])
    routes[eid] = routes[eid][:vpos] + [req] + routes[eid][vpos + 1:]
    routes[oid] = routes[oid][:opos] + [victim] + routes[oid][opos:]
    return True


def solve_improved(region: str, dist: str, requests: list[Request], engineers: list[Engineer]):
    eng: dict[str, Engineer] = {e.id: e for e in engineers}
    routes: dict[str, list[Request]] = {e.id: [] for e in engineers}
    cur_km: dict[str, float] = {e.id: 0.0 for e in engineers}
    unassigned: list[UnassignedReason] = []

    for req in _ordered(requests):
        best = None  # (eng_id, pos, added_km)
        for e in engineers:
            if req.skill not in e.skills:
                continue
            if req.required_transport and e.transport != req.required_transport:
                continue
            cur = routes[e.id]
            for pos in range(len(cur) + 1):
                trial = cur[:pos] + [req] + cur[pos:]
                sr = core.schedule_route(e, trial, dist)
                if sr is None:
                    continue
                added = sr["km"] - cur_km[e.id]
                if best is None or (added, pos) < (best[2], best[1]):
                    best = (e.id, pos, added)
        if best is None:
            unassigned.append(reasons.classify(req, engineers, reasons.Context(busy=routes, dist=dist)))
            continue
        eid, pos, _ = best
        routes[eid].insert(pos, req)
        sr = core.schedule_route(eng[eid], routes[eid], dist)
        cur_km[eid] = sr["km"]

    _rescue_urgent(requests, engineers, eng, routes, cur_km, unassigned, dist)

    # Локальный 2-opt: переворот подотрезков маршрута, если сокращает пробег и окна держатся.
    for e in engineers:
        r = routes[e.id]
        if len(r) < 3:
            continue
        improved = True
        while improved:
            improved = False
            for i in range(len(r)):
                for j in range(i + 2, len(r)):
                    candidate = r[:i] + list(reversed(r[i:j + 1])) + r[j + 1:]
                    sr = core.schedule_route(e, candidate, dist)
                    cur = core.schedule_route(e, r, dist)
                    if sr and cur and sr["km"] + 1e-6 < cur["km"]:
                        r = candidate
                        improved = True
            routes[e.id] = r

    final = [core.with_route(eng[e.id], routes[e.id], dist) for e in engineers]
    return core.build_plan(region, "improved", dist, final, unassigned, requests)