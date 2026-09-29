"""Базовый план (ТЗ 2.3): заявки в порядке поступления (как в файле),
назначаются первому доступному инженеру; порядок в маршруте = порядок назначения."""

from app.solvers import core, reasons
from app.schemas import Engineer, Request, UnassignedReason


def solve_fifo(region: str, dist: str, requests: list[Request], engineers: list[Engineer]):
    eng_routes: dict[str, list[Request]] = {e.id: [] for e in engineers}
    eng: dict[str, Engineer] = {e.id: e for e in engineers}
    unassigned: list[UnassignedReason] = []

    for req in requests:
        placed = False
        for e in engineers:
            if req.skill not in e.skills:
                continue
            if req.required_transport and e.transport != req.required_transport:
                continue
            trial = eng_routes[e.id] + [req]
            if core.feasible(e, trial):
                eng_routes[e.id] = trial
                placed = True
                break
        if not placed:
            unassigned.append(reasons.classify(req, engineers, reasons.Context(busy=eng_routes, dist=dist)))

    final = [core.with_route(eng[e.id], eng_routes[e.id], dist) for e in engineers]
    return core.build_plan(region, "baseline_fifo", dist, final, unassigned, requests)