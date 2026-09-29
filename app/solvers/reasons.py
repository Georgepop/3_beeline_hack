"""Почему заявка осталась неназначенной — в терминах ТЗ 2.2.

ТЗ требует назвать причину «понятным диспетчеру языком» и приводит ровно четыре
варианта: нет свободных исполнителей на требуемое время, нет навыка, нет
исполнителя с нужным транспортом, работа не помещается в окно/смену. Раньше все
временные отказы склеивались в одну фразу «не помещается в рабочие окна и смену»,
и диспетчер не мог отличить «заявку невозможно выполнить в принципе» от
«смена уже занята» — а это разные действия.

Поэтому здесь не одна строка, а код + текст + деталь с числами: сколько
подходящих исполнителей, когда ближайший освобождается, какое окно не хватило.
Классификация детерминированная и не пересчитывает план: она только читает уже
принятое решение, поэтому одинакова для fifo / improved / ortools.
"""

from dataclasses import dataclass, field

from app import distance as ds
from app.regions import TRANSPORT
from app.schemas import Engineer, Request, UnassignedReason
from app.solvers import core

NO_COORDS = "no_coords"
NO_SKILL = "no_skill"
NO_TRANSPORT = "no_transport"
OUTSIDE_SHIFT = "outside_shift"
NO_CAPACITY = "no_capacity"
SOLVER_LIMIT = "solver_limit"
MODEL_MISMATCH = "model_mismatch"

REASON_CODES = [
    NO_COORDS,
    NO_SKILL,
    NO_TRANSPORT,
    OUTSIDE_SHIFT,
    NO_CAPACITY,
    SOLVER_LIMIT,
    MODEL_MISMATCH,
]


@dataclass
class Context:
    """Состояние плана на момент отказа: что уже назначено и как считаем дорогу.

    Без загрузки инженеров отказ «нет свободных исполнителей» нечем подтвердить,
    поэтому солверы, которые назначают по одной заявке, передают накопленные
    маршруты.
    """
    busy: dict[str, list[Request]] = field(default_factory=dict)
    dist: str = "haversine"


def can_serve(req: Request, eng: Engineer) -> bool:
    """Проверка обязательных ограничений «навык» и «транспорт» (ТЗ 2.2) для одного исполнителя."""
    return req.skill in eng.skills and (not req.required_transport or eng.transport == req.required_transport)


def eligible(req: Request, engs: list[Engineer]) -> list[Engineer]:
    """Инженеры, проходящие обязательные ограничения «навык» и «транспорт» (ТЗ 2.2)."""
    return [e for e in engs if can_serve(req, e)]


def _transport_label(code: str) -> str:
    return TRANSPORT.get(code, {}).get("label", code)


def _free_at(eng: Engineer, ctx: Context) -> int:
    """Момент (мин от полуночи), когда инженер освободится по текущему маршруту."""
    route = ctx.busy.get(eng.id) or []
    if not route:
        return ds.parse_hhmm(eng.shift_start)
    sr = core.schedule_route(eng, route, ctx.dist)
    if sr is None:  # маршрут не пересобирается — считаем, что занят до конца смены
        return ds.parse_hhmm(eng.shift_end)
    return ds.parse_hhmm(eng.shift_start) + sr["minutes"]


def _best_case(req: Request, eng: Engineer, dist: str) -> tuple[int, int, int]:
    """Самое раннее (arrival, start, finish) для заявки как первой работы в смене."""
    shift_a = ds.parse_hhmm(eng.shift_start)
    leg = ds.distance_km(eng.start, core.req_point(req))
    if eng.speed_kph:
        travel = ds.travel_minutes_speed(leg, eng.speed_kph)
    else:
        travel = ds.travel_minutes_km(leg, eng.transport)
    arrival = shift_a + travel
    start = max(arrival, ds.parse_hhmm(req.window_start))
    return arrival, start, start + req.duration_min


def _shift_detail(req: Request, engs: list[Engineer], dist: str) -> str:
    """Почему заявка не влезает даже самым удачным инженером и первой в маршруте."""
    if not engs:
        return "Подходящих исполнителей в регионе нет."
    arrival, start, finish = min((_best_case(req, e, dist) for e in engs), key=lambda x: x[2])
    shift_end = min(ds.parse_hhmm(e.shift_end) for e in engs)
    wend = ds.parse_hhmm(req.window_end)
    if start > wend:
        return (f"Даже самый быстрый исполнитель приезжает в {ds.to_hhmm(arrival)}, "
                f"а окно заявки закрывается в {ds.to_hhmm(wend)} — начать работу в окне уже нельзя.")
    if finish > shift_end:
        return (f"Работа не помещается в смену: самый ранний вариант — старт "
                f"{ds.to_hhmm(start)}, конец {ds.to_hhmm(finish)}, а смена заканчивается в "
                f"{ds.to_hhmm(shift_end)} (норматив {req.duration_min} мин).")
    return (f"Приезда и окна хватает только при поездке без очереди: самый ранний финиш "
            f"{ds.to_hhmm(finish)} в смену до {ds.to_hhmm(shift_end)}.")


def classify(req: Request, engs: list[Engineer], ctx: Context | None = None) -> UnassignedReason:
    """Классифицирует отказ и собирает запись для плана."""
    ctx = ctx or Context()
    ok = eligible(req, engs)

    if req.lat is None or req.lng is None:
        code = NO_COORDS
        text = "Нет координат — адрес не геокодирован"
        detail = "Заявку нельзя поставить на карту и посчитать дорогу; нужен геокод адреса."
    elif not [e for e in engs if req.skill in e.skills]:
        code = NO_SKILL
        text = f"Нет инженера с навыком «{req.skill_label}»"
        detail = f"Навык «{req.skill_label}» не входит в навыки ни одного из {len(engs)} исполнителей региона."
    elif req.required_transport and not ok:
        code = NO_TRANSPORT
        label = _transport_label(req.required_transport)
        with_transport = [e for e in engs if e.transport == req.required_transport]
        text = f"Нет исполнителя с навыком «{req.skill_label}» и транспортом «{label}»"
        detail = (f"Транспорт «{label}» есть у {len(with_transport)} из {len(engs)} исполнителей, "
                  f"но нужного навыка среди них нет.")
    elif all(core.schedule_route(e, [req], ctx.dist) is None for e in ok):
        code = OUTSIDE_SHIFT
        text = "Работа не помещается в рабочее окно и смену инженеров"
        detail = _shift_detail(req, ok, ctx.dist)
    else:
        code = NO_CAPACITY
        text = "Нет свободных исполнителей на требуемое время"
        can_alone = [e for e in ok if core.schedule_route(e, [req], ctx.dist) is not None]
        if not ok:
            detail = f"Подходящих исполнителей в смене нет (проверено {len(engs)})."
        elif not ctx.busy:
            detail = (f"Подходящих исполнителей {len(ok)}, но ни один не успевает выполнить заявку "
                      f"в окно {req.window_start}–{req.window_end}.")
        elif not can_alone:
            detail = (f"Ни один из {len(ok)} подходящих исполнителей не успевает доехать и выполнить "
                      f"заявку в окно {req.window_start}–{req.window_end}.")
        else:
            # Кто физически мог бы взять её — и когда эти инженеры реально освобождаются.
            kinds = sorted({_transport_label(e.transport) for e in can_alone})
            kind = f" с транспортом «{kinds[0]}»" if len(kinds) == 1 else ""
            free_at = sorted(_free_at(e, ctx) for e in can_alone)
            span = (ds.to_hhmm(free_at[0]) if len(free_at) == 1
                    else f"{ds.to_hhmm(free_at[0])}–{ds.to_hhmm(free_at[-1])}")
            detail = (f"В одиночку заявку могли бы взять {len(can_alone)} из {len(ok)} подходящих{kind}: "
                      f"у остальных дорога не помещается в окно {req.window_start}–{req.window_end}. "
                      f"В текущем плане их маршруты заканчиваются в {span}, "
                      f"а окно закрывается в {req.window_end} — "
                      f"не хватает времени в смене, а не навыка или транспорта.")

    return UnassignedReason(
        request_id=req.id,
        address=req.address,
        reason=text,
        reason_code=code,
        reason_detail=detail,
        window=[req.window_start, req.window_end],
        skill_label=req.skill_label,
    )


def note(req: Request, text: str, code: str) -> UnassignedReason:
    """Отказ, о котором солвер знает сам (таймаут, расхождение модели), а не причина ТЗ."""
    return UnassignedReason(
        request_id=req.id,
        address=req.address,
        reason=text,
        reason_code=code,
        reason_detail=text,
        window=[req.window_start, req.window_end],
        skill_label=req.skill_label,
    )


def hint(req: Request, engs: list[Engineer], code: str, ctx: Context | None = None) -> str:
    """Что конкретно помогло бы выполнить заявку — из тех же чисел, что и причина.

    Диспетчеру нужен не только вердикт, но и следующий шаг: сдвинуть окно,
    продлить смену, выдать другую технику. Ничего не придумываем: подсказка
    считается от тех же окон, нормативов и транспорта, что дали отказ.
    """
    ctx = ctx or Context()
    ok = eligible(req, engs)
    if code == NO_COORDS:
        return "Помогло бы: задать координаты заявки — адрес не распознан геокодером."
    if code == NO_SKILL:
        return f"Помогло бы: добавить исполнителя с навыком «{req.skill_label}» или переназначить заявку на другой тип работ."
    if code == NO_TRANSPORT:
        return (f"Помогло бы: исполнитель с навыком «{req.skill_label}» и транспортом "
                f"«{_transport_label(req.required_transport)}».")
    if code == OUTSIDE_SHIFT and ok:
        arrival, start, finish = min((_best_case(req, e, ctx.dist) for e in ok), key=lambda x: x[2])
        wend = ds.parse_hhmm(req.window_end)
        shift_end = min(ds.parse_hhmm(e.shift_end) for e in ok)
        if start > wend:
            return f"Помогло бы: перенести окно заявки не раньше {ds.to_hhmm(arrival)} (сейчас закрывается в {ds.to_hhmm(wend)})."
        if finish > shift_end:
            over = finish - shift_end
            return (f"Помогло бы: продлить смену до {ds.to_hhmm(finish)} "
                    f"(не хватает {over} мин) или сократить норматив на {over} мин.")
    if code == NO_CAPACITY and ok:
        can_alone = [e for e in ok if core.schedule_route(e, [req], ctx.dist) is not None]
        if can_alone:
            free_at = sorted(_free_at(e, ctx) for e in can_alone)
            earliest = free_at[0]
            wend = ds.parse_hhmm(req.window_end)
            kinds = sorted({_transport_label(e.transport) for e in can_alone})
            kind = f" с транспортом «{kinds[0]}»" if len(kinds) == 1 else ""
            if earliest < wend:
                # Освободились до закрытия окна — сдвиг окна реально помогает.
                return (f"Помогло бы: перенести окно заявки на {ds.to_hhmm(earliest)}–{req.window_end} — "
                        f"тогда её смогут взять {len(can_alone)} исполнител(ей){kind}.")
            # Освобождаются уже после закрытия окна: сдвиг окна не поможет, нужен ресурс.
            return (f"Помогло бы: дополнительный исполнитель{kind} — окно заявки закрывается в "
                    f"{req.window_end}, а подходящие инженеры освобождаются только к "
                    f"{ds.to_hhmm(earliest)}.")
        return f"Помогло бы: укрупнить окно заявки или сократить норматив — дорога не помещается в {req.duration_min} мин."
    return ""
