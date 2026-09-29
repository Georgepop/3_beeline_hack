"""Бенчмарк-план на OR-Tools Routing — тот же контракт PlanResponse, что у fifo/improved.

Ключевые отличия от «сырой» реализации (учебный прототип):
- та же модель времени, что в core.schedule_route (те же скорости/окна/нормативы),
  поэтому таймлайны сходятся с improved до минуты и метрики сопоставимы;
- единая целевая функция — минуты (не «метры / скорость», из-за чего дуги разных
  инженеров становились несопоставимы между собой);
- штраф за пропуск узла + явный отчёт по каждой неназначенной заявке с причиной;
- стабильная идентификация заявок по request_id, а не по позиционному индексу;
- невыполнимое решение не роняет планирование, а даёт план с объяснением.
"""

from app.config import get_settings
from app.distance import distance_km, parse_hhmm, travel_minutes_km, travel_minutes_speed
from app.schemas import Engineer, LatLng, Request
from app.solvers import core, reasons
from app.solvers.core import req_point

MODE = "benchmark_ortools"
HORIZON = 24 * 60  # потолок временно́й размерности, мин от полуночи (в смене он не достигается)


def is_available() -> bool:
    """Пакет ortools ставится опционально (requirements-benchmark.txt) — проверяем на лету."""
    try:
        from ortools.constraint_solver import pywrapcp  # noqa: F401
    except Exception:
        return False
    return True


def _win(req: Request, s) -> tuple[int, int]:
    """Окно заявки в минутах от полуночи; пустое/битое -> границы смены."""
    a = parse_hhmm(req.window_start) if req.window_start else parse_hhmm(s.shift_start)
    b = parse_hhmm(req.window_end) if req.window_end else parse_hhmm(s.shift_end)
    if b < a:  # ночное окно в пределах смены не моделируем
        b = a
    return a, b


def _travel_for(eng: Engineer, km: float) -> int:
    if eng.speed_kph:
        return travel_minutes_speed(km, eng.speed_kph)
    return travel_minutes_km(km, eng.transport)


def _eligible(req: Request, engineers: list[Engineer]) -> bool:
    return any(reasons.can_serve(req, e) for e in engineers)


def solve_ortools(region: str, dist: str, requests: list[Request], engineers: list[Engineer]):
    s = get_settings()

    # В модель попадают только заявки с координатами, которые может взять хоть один инженер.
    # Остальные получают причину из reasons.classify ниже — по одному месту на все случаи.
    served = [r for r in requests
              if r.lat is not None and r.lng is not None and _eligible(r, engineers)]

    if not engineers or not served:
        unassigned = [reasons.classify(r, engineers) for r in requests]
        return core.build_plan(region, MODE, dist, [], unassigned, requests)

    from ortools.constraint_solver import pywrapcp, routing_enums_pb2

    depot = engineers[0].start
    nodes: list[LatLng] = [LatLng(lat=depot.lat, lng=depot.lng)] + [req_point(r) for r in served]
    n = len(nodes)

    # Км-матрица один раз; минуты — свои для каждого инженера (его транспорт/скорость).
    km = [[0.0] * n for _ in range(n)]
    for i in range(n):
        for j in range(n):
            if i != j:
                km[i][j] = distance_km(nodes[i], nodes[j])
    tmat = [[[_travel_for(e, km[i][j]) for j in range(n)] for i in range(n)] for e in engineers]

    def service_min(node: int) -> int:
        return 0 if node <= 0 else served[node - 1].duration_min

    manager = pywrapcp.RoutingIndexManager(n, len(engineers), 0)
    routing = pywrapcp.RoutingModel(manager)

    # Кто какую заявку может взять (навык + требуемый транспорт).
    # -1 в домене — служебное значение «узел не посещён»: без него SetValues делает
    # заявку обязательной, и модель становится неразрешимой, если взять её нечем.
    for i, req in enumerate(served, start=1):
        allowed = [v for v, e in enumerate(engineers) if _eligible(req, [e])]
        routing.VehicleVar(manager.NodeToIndex(i)).SetValues([-1] + allowed)

    # Стоимость дуги — только время в пути; плата за каждого реально задействованного
    # инженера задана SetFixedCostOfVehicle, чтобы план не схлопывался на одном.
    # Возврата в офис нет (как в improved), поэтому дуга в «конец маршрута» нулевая.
    for v in range(len(engineers)):
        tm = tmat[v]

        def cost_cb(from_index, to_index, tm=tm):
            to = manager.IndexToNode(to_index)
            if to < 0:
                return 0
            return tm[manager.IndexToNode(from_index)][to]

        cb = routing.RegisterTransitCallback(cost_cb)
        routing.SetArcCostEvaluatorOfVehicle(cb, v)
        routing.SetFixedCostOfVehicle(s.ortools_fixed_vehicle_cost, v)  # (cost, vehicle) в этом биндинге

    transit_callbacks = []
    for v in range(len(engineers)):
        tm = tmat[v]

        def time_cb(from_index, to_index, tm=tm):
            # В OR-Tools cumul узла = момент ПРИБЫТИЯ (= начала работ, окно можно ждать),
            # поэтому длительность заявки входит в дугу ИЗ этого узла, а не в неё.
            frm = manager.IndexToNode(from_index)
            to = manager.IndexToNode(to_index)
            if to < 0:  # конец маршрута: возврата в офис нет, только остаток работ
                return service_min(frm)
            return service_min(frm) + tm[frm][to]

        transit_callbacks.append(routing.RegisterTransitCallback(time_cb))

    # Горизонт размерности = сдвиг/окно + работы с запасом: жёсткий потолок ниже
    # не должен молча делать позднее окно невыполнимым.
    horizon = max(HORIZON,
                  max((_win(r, s)[1] for r in served), default=0) + max((r.duration_min for r in served), default=0))
    routing.AddDimensionWithVehicleTransits(
        transit_callbacks,
        s.ortools_slack_max,   # сколько можно ждать до начала окна
        horizon,              # потолок cumul (ёмкости тут нет — это граница времени, не груз)
        False,                 # старт фиксируем сами, ниже
        "Time",
    )
    tdim = routing.GetDimensionOrDie("Time")
    for v, eng in enumerate(engineers):
        shift_a = parse_hhmm(eng.shift_start or s.shift_start)
        shift_b = parse_hhmm(eng.shift_end or s.shift_end)
        tdim.CumulVar(routing.Start(v)).SetRange(shift_a, shift_a)
        tdim.CumulVar(routing.End(v)).SetRange(shift_a, shift_b)
    for i, req in enumerate(served, start=1):
        tdim.CumulVar(manager.NodeToIndex(i)).SetRange(*_win(req, s))
    # Штраф за пропуск заявки разный для срочных (ТЗ 2.2: срочность важнее).
    # Повышенный штраф заставляет решатель жертвовать обычной заявкой, а не аварийной,
    # но остаётся конечным — это не запрет: если срочную взять нечем, она уйдёт
    # в неназначенные с причиной, а не сломает планирование.
    urgent_penalty = s.ortools_drop_penalty * max(1, s.ortools_urgent_penalty_factor)
    for i, req in enumerate(served, start=1):
        penalty = urgent_penalty if req.priority == "urgent" else s.ortools_drop_penalty
        routing.AddDisjunction([manager.NodeToIndex(i)], penalty)

    params = pywrapcp.DefaultRoutingSearchParameters()
    params.first_solution_strategy = getattr(
        routing_enums_pb2.FirstSolutionStrategy, s.ortools_first_solution.upper())
    params.local_search_metaheuristic = getattr(
        routing_enums_pb2.LocalSearchMetaheuristic, s.ortools_local_search.upper())
    params.time_limit.FromSeconds(s.ortools_time_limit)
    # В ortools 9.15 у RoutingSearchParameters нет поля seed, поэтому результат
    # зависит от того, сколько итераций машина успеет сделать за отведённое
    # wall-clock время. На одном лимите в одной сессии повторы совпадали, но при
    # разной загрузке наблюдался разброс в 1 заявку — потому лимит и урезан до
    # 5 с (app/config.py): выигрыш от 30 с неустойчив и не стоит ожидания.
    params.log_search = False
    solution = routing.SolveWithParameters(params)

    if solution is None:
        why = f"Решение не найдено за {s.ortools_time_limit} с (ограничения по окнам несовместимы)"
        unassigned = [reasons.note(r, why, reasons.SOLVER_LIMIT) for r in served]
        return core.build_plan(region, MODE, dist, [], unassigned, requests)

    routes: dict[str, list[Request]] = {e.id: [] for e in engineers}
    for v, eng in enumerate(engineers):
        index = routing.Start(v)
        while not routing.IsEnd(index):
            node = manager.IndexToNode(index)
            if node != 0:
                routes[eng.id].append(served[node - 1])
            index = solution.Value(routing.NextVar(index))

    done = {r.id for rs in routes.values() for r in rs}
    unassigned = [reasons.classify(r, engineers, reasons.Context(busy=routes, dist=dist))
                  for r in requests if r.id not in done]

    final = []
    for e in engineers:
        reqs = routes[e.id]
        eng = core.with_route(e, reqs, dist)
        if reqs and not eng.stops:
            # Модель разошлась с core (не должно быть) — не теряем заявки молча.
            for r in reqs:
                unassigned.append(reasons.note(
                    r, "Маршрут из OR-Tools не прошёл проверку core — заявка снята с назначения",
                    reasons.MODEL_MISMATCH))
        final.append(eng)
    return core.build_plan(region, MODE, dist, final, unassigned, requests)
