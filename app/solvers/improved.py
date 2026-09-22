"""Улучшенный план: срочные заявки первыми → insertion (лучший инженер + позиция
по добавочному пробегу) → локальный 2-opt по каждому маршруту (с проверкой окон)."""

from app.solvers import core
from app.schemas import Engineer, Request, UnassignedReason


def _ordered(reqs: list[Request]) -> list[Request]:
    return sorted(reqs, key=lambda r: (0 if r.priority == "urgent" else 1, core.ds.parse_hhmm(r.window_start)))


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
            unassigned.append(UnassignedReason(
                request_id=req.id,
                address=req.address,
                reason=core.reason_for(req, engineers),
                window=[req.window_start, req.window_end],
                skill_label=req.skill_label,
            ))
            continue
        eid, pos, _ = best
        routes[eid].insert(pos, req)
        sr = core.schedule_route(eng[eid], routes[eid], dist)
        cur_km[eid] = sr["km"]

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