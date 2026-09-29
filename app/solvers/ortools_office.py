"""Режим «Контур» (ortools_office) — вариант OR-Tools с платным возвратом в офис.

Солвер-собственник: намеренно НЕ делит код с benchmark_ortools. Общие у них были
бы вспомогательные функции, но любая правка в общем коде цепляет и режим, который
посчитан, зафиксирован в README и используется как точка отсчёта. Здесь всё своё,
app/solvers/ortools_solver.py не трогаем вообще.

Чем отличается от benchmark_ortools (сравнивать имеет смысл только с ним — оба на
одном OR-Tools, оба на окнах):

1. Возврат в офис платный. Депо — узел 0, конец маршрута тоже на него замыкается,
   поэтому дуга «последняя заявка → офис» оплачивается временем и упирается в
   конец смены. В benchmark_ortools дуга в конец обнулена. Отсюда главное
   содержательное отличие режима: инженер возвращается домой, и это стоит заявок.
2. Длины дуг — дорожная матрица OSRM, а не haversine. Матрица направленная
   (односторонки в Москве разводят расстояния в разы на близких парах), и время
   в пути считается по ней же. При недоступной сети режим молча уходит на
   haversine и честно пишет, чем считал, в PlanResponse.matrix.
3. Своя первая стратегия PATH_CHEAPEST_ARC вместо parallel_cheapest_insertion.
4. Штраф за простой: SetCumulVarSoftUpperBound на начало окна. Прибыл раньше и
   ждёшь — копится штраф, и решатель предпочитает загруженный день ожиданию.
5. Градуированная важность заявки по типу (их prior, 1:1) вместо наших двух
   уровней «срочная/обычная».

Про длительности: их таблица Equip_dick даёт «Глобальной проблеме» 100 минут
против наших 40 по ТЗ. Это норматив, а не алгоритм, поэтому здесь остаётся
request.duration_min из импорта — иначе метрики «Контура» нельзя ставить рядом
с FIFO/improved/benchmark_ortools.

Про штраф за пропуск: в OR-Tools AddDisjunction(node, penalty) — это цена ОТКАЗА
от узла, поэтому чем больше вес, тем труднее снять заявку с маршрута. «Глобальная
проблема» с весом 1000 де-факто обязательна, «Локальная заявка» и «Дозаказ» (1)
отбрасываются первыми. Это согласуется с нашим ТЗ 2.2 (срочность важнее), просто
градация у нас была двухступенчатой.
"""

from app import distance as ds
from app import osrm
from app.config import get_settings
from app.schemas import Engineer, LatLng, Request, RoadStop
from app.solvers import core, reasons
from app.solvers.core import req_point
from app.solvers.ortools_solver import _eligible, _travel_for, _win

MODE = "ortools_office"

# Важность заявки по типу BK: во сколько раз дороже отказ. Вес 1000 делает
# «Глобальную проблему» фактически обязательной.
IMPORTANCE = {
    "Локальная заявка": 1,
    "Дозаказ": 1,
    "Подключение": 5,
    "Глобальная проблема": 1000,
}
DEFAULT_IMPORTANCE = 1

_HORIZON = 24 * 60  # потолок временно́й размерности, мин от полуночи


def is_available() -> bool:
    """Как и у benchmark_ortools: пакет ortools ставится опционально."""
    try:
        from ortools.constraint_solver import pywrapcp  # noqa: F401
    except Exception:
        return False
    return True


def _distance_matrix(nodes: list[LatLng]) -> tuple[list[list[float]], str]:
    """Матрица длин дуг в км и подпись, чем она посчитана (для PlanResponse.matrix)."""
    s = get_settings()
    if s.ortools_office_matrix == "osrm":
        try:
            got = osrm.table_matrix_km([(nd.lat, nd.lng) for nd in nodes])
        except Exception:
            got = None
        if got is not None and len(got) == len(nodes):
            return got, "osrm"
    # Откат на haversine: лучше посчитать приближённо, чем не отдать план вообще.
    # Метка в matrix нужна, чтобы расхождение цифр при недоступной сети было видно.
    return [[ds.distance_km(nodes[i], nodes[j]) for j in range(len(nodes))]
            for i in range(len(nodes))], "haversine"


def solve_office(region: str, dist: str, requests: list[Request], engineers: list[Engineer]):
    s = get_settings()

    served = [r for r in requests
              if r.lat is not None and r.lng is not None and _eligible(r, engineers)]
    if not engineers or not served:
        unassigned = [reasons.classify(r, engineers) for r in requests]
        return core.build_plan(region, MODE, dist, [], unassigned, requests)

    from ortools.constraint_solver import pywrapcp, routing_enums_pb2

    depot = engineers[0].start
    nodes: list[LatLng] = [LatLng(lat=depot.lat, lng=depot.lng)] + [req_point(r) for r in served]
    n = len(nodes)
    km, matrix_source = _distance_matrix(nodes)

    # Время в пути у каждого инженера своё (его транспорт или скорость).
    tmat = [[[_travel_for(e, km[i][j]) for j in range(n)] for i in range(n)] for e in engineers]
    longest_leg = max((max(row[0] for row in tm) for tm in tmat), default=0)

    def service_min(node: int) -> int:
        return 0 if node <= 0 else served[node - 1].duration_min

    # Кто какую заявку может взять. -1 в домене — служебное «узел не посещён»:
    # без него заявка, которую взять нечем, делает модель неразрешимой.
    allowed_by_node: dict[int, list[int]] = {}
    for i, req in enumerate(served, start=1):
        allowed_by_node[i] = [v for v, e in enumerate(engineers) if _eligible(req, [e])]

    manager = pywrapcp.RoutingIndexManager(n, len(engineers), 0)
    routing = pywrapcp.RoutingModel(manager)
    for i, allowed in allowed_by_node.items():
        routing.VehicleVar(manager.NodeToIndex(i)).SetValues([-1] + allowed)

    # Стоимость дуги — минуты в пути, плюс плата за каждого задействованного
    # инженера (иначе план схлопывается на одном).
    for v in range(len(engineers)):
        tm = tmat[v]

        def cost_cb(from_index, to_index, tm=tm):
            to = manager.IndexToNode(to_index)
            if to < 0:
                return 0
            return tm[manager.IndexToNode(from_index)][to]

        cb = routing.RegisterTransitCallback(cost_cb)
        routing.SetArcCostEvaluatorOfVehicle(cb, v)
        routing.SetFixedCostOfVehicle(s.ortools_fixed_vehicle_cost, v)

    transit_callbacks = []
    for v in range(len(engineers)):
        tm = tmat[v]

        def time_cb(from_index, to_index, tm=tm):
            # cumul узла = момент ПРИБЫТИЯ (окно можно ждать), поэтому длительность
            # заявки входит в дугу ИЗ этого узла. В отличие от benchmark_ortools дуга
            # в конец маршрута НЕ обнуляется: IndexToNode(end) даёт депо, то есть
            # возвращение в офис оплачивается временем. Это и есть «контур».
            frm = manager.IndexToNode(from_index)
            to = manager.IndexToNode(to_index)
            return service_min(frm) + tm[frm][to if to >= 0 else 0]

        transit_callbacks.append(routing.RegisterTransitCallback(time_cb))

    # Потолок размерности должен перекрывать не только окно, но и возвращение,
    # иначе конец смены окажется за горизонтом и встанет невыполнимым.
    horizon = max(_HORIZON,
                  max((_win(r, s)[1] for r in served), default=0)
                  + max((r.duration_min for r in served), default=0)
                  + longest_leg)
    routing.AddDimensionWithVehicleTransits(
        transit_callbacks,
        s.ortools_slack_max,   # сколько можно ждать до начала окна
        horizon,
        False,                 # старт фиксируем сами, ниже
        "Time",
    )
    tdim = routing.GetDimensionOrDie("Time")
    for v, eng in enumerate(engineers):
        shift_a = ds.parse_hhmm(eng.shift_start or s.shift_start)
        shift_b = ds.parse_hhmm(eng.shift_end or s.shift_end)
        tdim.CumulVar(routing.Start(v)).SetRange(shift_a, shift_a)
        tdim.CumulVar(routing.End(v)).SetRange(shift_a, shift_b)
    for i, req in enumerate(served, start=1):
        win_a, win_b = _win(req, s)
        tdim.CumulVar(manager.NodeToIndex(i)).SetRange(win_a, win_b)
        # Штраф за простой: cumul (момент прибытия) выше начала окна — это ожидание.
        if s.ortools_office_wait_penalty:
            tdim.SetCumulVarSoftUpperBound(manager.NodeToIndex(i), win_a, s.ortools_office_wait_penalty)

    # Штраф за пропуск — по градуированной важности типа заявки.
    for i, req in enumerate(served, start=1):
        if not allowed_by_node[i]:
            # Взять некому: их код добавляет disjunction с нулевым штрафом, узел
            # снимается всегда. Причина попадёт в unassigned через reasons.classify.
            routing.AddDisjunction([manager.NodeToIndex(i)], 0)
        else:
            weight = IMPORTANCE.get(req.bk_type, DEFAULT_IMPORTANCE)
            routing.AddDisjunction([manager.NodeToIndex(i)], s.ortools_drop_penalty * weight)

    params = pywrapcp.DefaultRoutingSearchParameters()
    params.first_solution_strategy = routing_enums_pb2.FirstSolutionStrategy.PATH_CHEAPEST_ARC
    params.local_search_metaheuristic = routing_enums_pb2.LocalSearchMetaheuristic.GUIDED_LOCAL_SEARCH
    params.time_limit.FromSeconds(s.ortools_time_limit)
    params.log_search = False
    solution = routing.SolveWithParameters(params)

    if solution is None:
        why = f"Решение не найдено за {s.ortools_time_limit} с (ограничения по окнам несовместимы)"
        unassigned = [reasons.note(r, why, reasons.SOLVER_LIMIT) for r in served]
        plan = core.build_plan(region, MODE, dist, [], unassigned, requests)
        plan.matrix = matrix_source
        return plan

    # Таймлайн собираем из самого решения, а не через core.with_route: core считает
    # время по haversine, из-за чего отчёт разошёлся бы с дорожной матрицей, по
    # которой считал решатель, и в нём нет возвращения в офис вообще.
    done: set[str] = set()
    final: list[Engineer] = []
    busy: dict[str, list[Request]] = {}
    for v, eng in enumerate(engineers):
        index = routing.Start(v)
        prev = 0
        stops: list[RoadStop] = []
        taken: list[Request] = []
        leg_km = 0.0
        while not routing.IsEnd(index):
            node = manager.IndexToNode(index)
            if node != 0:
                req = served[node - 1]
                leg_km += km[prev][node]
                arrival = solution.Min(tdim.CumulVar(index))
                win_a, win_b = _win(req, s)
                start_work = max(arrival, win_a)
                finish = start_work + req.duration_min
                stops.append(RoadStop(
                    step=len(stops) + 1,
                    request_id=req.id,
                    address=req.address,
                    lat=req.lat,
                    lng=req.lng,
                    arrival=ds.to_hhmm(arrival),
                    start_work=ds.to_hhmm(start_work),
                    finish=ds.to_hhmm(finish),
                    window=[ds.to_hhmm(win_a), ds.to_hhmm(win_b)],
                    duration_min=req.duration_min,
                ))
                done.add(req.id)
                taken.append(req)
                prev = node
            index = solution.Value(routing.NextVar(index))
        if not stops:
            continue
        # Возврат в офис: доезжаем от последней заявки до депо и считаем смену
        # по времени прибытия в офис — так это и видит диспетчер.
        leg_km += km[prev][0]
        back = solution.Min(tdim.CumulVar(routing.End(v)))
        shift_a = ds.parse_hhmm(eng.shift_start or s.shift_start)
        final.append(Engineer(
            id=eng.id, name=eng.name, skills=eng.skills, skills_label=eng.skills_label,
            transport=eng.transport, transport_label=eng.transport_label,
            speed_kph=eng.speed_kph,
            shift_start=eng.shift_start, shift_end=eng.shift_end,
            start=eng.start, route=None, stops=stops,
            km=round(leg_km, 1), minutes=max(0, back - shift_a),
        ))
        # reasons.classify пересобирает занятость инженера через core.schedule_route,
        # поэтому ему нужны Request, а не RoadStop, которые мы выше наполнили сами.
        busy[eng.id] = taken

    ctx = reasons.Context(busy=busy, dist=dist)
    unassigned = [reasons.classify(r, engineers, ctx) for r in requests if r.id not in done]
    plan = core.build_plan(region, MODE, dist, final, unassigned, requests)
    plan.matrix = matrix_source
    return plan
