"""Ядро солверов: общие вычисления маршрута, ограничения, метрики, причины.

Любой солвер работает с упорядоченным списком заявок и возвращает план.
"""

from app import distance as ds
from app.config import get_settings
from app.regions import REGIONS
from app.schemas import (
    Engineer,
    LatLng,
    PlanMetrics,
    PlanResponse,
    Request,
    RoadStop,
    UnassignedReason,
)


def req_point(req: Request) -> LatLng:
    return LatLng(lat=req.lat or 0, lng=req.lng or 0)


def schedule_route(eng: Engineer, reqs: list[Request], dist: str = "haversine"):
    """Считает таймлайн маршрута для инженера. Возвращает словарь или None, если не влезает."""
    s = get_settings()
    cur = eng.start
    time_min = ds.parse_hhmm(eng.shift_start)
    shift_end = ds.parse_hhmm(eng.shift_end)
    stops: list[RoadStop] = []
    km = 0.0
    for req in reqs:
        pt = req_point(req)
        leg = ds.distance_km(cur, pt)
        if eng.speed_kph:
            travel = ds.travel_minutes_speed(leg, eng.speed_kph)
        else:
            travel = ds.travel_minutes_km(leg, eng.transport)
        arrival = time_min + travel
        wstart = ds.parse_hhmm(req.window_start)
        wend = ds.parse_hhmm(req.window_end)
        start_work = max(arrival, wstart)
        finish = start_work + req.duration_min
        if start_work > wend or finish > shift_end:
            return None
        stops.append(RoadStop(
            step=len(stops) + 1,
            request_id=req.id,
            address=req.address,
            lat=pt.lat,
            lng=pt.lng,
            arrival=ds.to_hhmm(arrival),
            start_work=ds.to_hhmm(start_work),
            finish=ds.to_hhmm(finish),
            window=[req.window_start, req.window_end],
            duration_min=req.duration_min,
        ))
        cur = pt
        time_min = finish
        km += leg
    return {"stops": stops, "km": km, "minutes": time_min - ds.parse_hhmm(eng.shift_start)}


def feasible(eng: Engineer, reqs: list[Request]) -> bool:
    return schedule_route(eng, reqs) is not None


def shift_ceiling(reqs: list[Request], engs: list[Engineer], dist: str = "haversine") -> int:
    """Сколько заявок смена способна выполнить в принципе (ТЗ 2.2 — жёсткие окна).

    Заявка входит в потолок, только если есть координаты, есть инженер с нужным
    навыком и транспортом, и она помещается в смену целиком — как единственная
    первая работа. Всё остальное упирается в занятость инженеров, а не в модель
    ограничений, поэтому именно эта цифра показывает, что улучшать дальше.
    """
    ok = 0
    for req in reqs:
        if req.lat is None or req.lng is None:
            continue
        # Проверка навыка и транспорта продублирована здесь намеренно: reasons
        # импортирует core, и обратный импорт замкнул бы цикл.
        fits = any(schedule_route(e, [req], dist) is not None for e in engs
                   if req.skill in e.skills
                   and (not req.required_transport or e.transport == req.required_transport))
        if fits:
            ok += 1
    return ok


def build_plan(region: str, mode: str, dist: str, engineers: list[Engineer],
               unassigned: list[UnassignedReason], requests: list[Request]) -> PlanResponse:
    active = [e for e in engineers if e.stops]
    total_km = 0.0
    total_min = 0
    for e in active:
        total_km += e.km
        total_min += e.minutes
    metrics = PlanMetrics(
        total_requests=len(requests),
        assigned_count=sum(len(e.stops) for e in active),
        unassigned_count=len(unassigned),
        engineers_used=len(active),
        total_km=round(total_km, 1),
        total_minutes=total_min,
    )
    return PlanResponse(
        region=region,
        region_name=REGIONS[region]["name"],
        mode=mode,
        date="2026-08-17",  # день смены из датасета
        dist=dist,
        engineers=active,
        unassigned=unassigned,
        metrics=metrics,
    )


def with_route(eng: Engineer, reqs: list[Request], dist: str) -> Engineer:
    sr = schedule_route(eng, reqs, dist)
    if sr is None:
        return eng
    route = None
    if get_settings().osrm_enabled and sr["stops"]:
        from app import osrm

        route = osrm.route_polyline(
            [(eng.start.lat, eng.start.lng)] + [(st.lat, st.lng) for st in sr["stops"]]
        )
    return Engineer(
        id=eng.id, name=eng.name, skills=eng.skills, skills_label=eng.skills_label,
        transport=eng.transport, transport_label=eng.transport_label,
        shift_start=eng.shift_start, shift_end=eng.shift_end,
        start=eng.start, route=route, stops=sr["stops"], km=round(sr["km"], 1), minutes=sr["minutes"],
    )