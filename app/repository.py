"""Единственная точка доступа к данным.

Наружу отдаёт те же pydantic-модели, что и раньше (app/schemas.py), поэтому
потребителям — planner, api, bench — не нужно знать, откуда взяты данные.
Отпечаток app/session.py считается по этим же моделям, поэтому любая правка в
БД меняет отпечаток и кеш плана промахивается сам, без ручных сбросов.
"""

from sqlalchemy import delete, func, select

from app.db import session_scope
from app.models import (
    EngineerBaseline,
    EngineerRow,
    Region,
    RequestBaseline,
    RequestRow,
)
from app.regions import SKILLS, TRANSPORT, norm_min, skill_of
from app.schemas import Engineer, LatLng, Request, RegionMeta

_REQ_COLS = (
    "id", "bk_type", "hd_type", "skill", "window_start", "window_end",
    "district", "address", "tech", "gigabit", "priority", "duration_min",
    "required_transport", "status", "control_brigade", "lat", "lng",
)

_ENG_COLS = (
    "id", "name", "skills", "transport", "speed_kph",
    "shift_start", "shift_end", "lat", "lng",
)


def _to_request(row) -> Request:
    kw = {c: getattr(row, c) for c in _REQ_COLS}
    kw["skill_label"] = SKILLS.get(row.skill, {}).get("label", row.skill or "")
    return Request(**kw)


def _to_engineer(row) -> Engineer:
    from app.regions import REGIONS

    skills = list(row.skills or [])
    # Точка старта инженера — это офис региона, он общий для всех бригад, поэтому
    # подпись не хранится, а восстанавливается из справочника.
    office = (REGIONS.get(row.region) or {}).get("office")
    return Engineer(
        id=row.id,
        name=row.name,
        skills=skills,
        skills_label=[SKILLS.get(s, {}).get("label", s) for s in skills],
        transport=row.transport or "",
        transport_label=TRANSPORT.get(row.transport or "", {}).get("label", ""),
        speed_kph=row.speed_kph,
        shift_start=row.shift_start or "08:00",
        shift_end=row.shift_end or "20:00",
        start=LatLng(lat=row.lat, lng=row.lng, name=office.name if office else ""),
    )


def _natural_key(row) -> tuple:
    """Вторичный ключ сортировки: номер из id, а не сам id строкой.

    Идентификаторы нумеруются и выглядят как 'eng-vostok-10', поэтому обычная
    сортировка по строке даёт 1, 10, 11, 12, 2… и тихо ухудшает план. Здесь
    число вытаскивается явно, и даже если ordinal окажется у всех одинаковым
    (например, после переноса старой базы), порядок останется верным.
    """
    ident = row.id or ""
    tail = ident.rsplit("-", 1)[-1]
    return (0, int(tail), ident) if tail.isdigit() else (1, 0, ident)


def requests_for(region: str, active: bool = True) -> list[Request]:
    with session_scope() as s:
        stmt = select(RequestRow).where(RequestRow.region == region)
        if active:
            stmt = stmt.where(RequestRow.is_active.is_(True))
        # Порядок поступления (ordinal) важен: солверы к нему чувствительны
        # (ТЗ — «заявки обрабатываются по порядку поступления»). См. _natural_key.
        rows = list(s.scalars(stmt))
        rows.sort(key=lambda r: (r.ordinal, _natural_key(r)))
        return [_to_request(r) for r in rows]


def engineers_for(region: str) -> list[Engineer]:
    with session_scope() as s:
        rows = list(
            s.scalars(select(EngineerRow).where(EngineerRow.region == region))
        )
        rows.sort(key=lambda r: (r.ordinal, _natural_key(r)))
        return [_to_engineer(r) for r in rows]


def region_meta(region: str) -> RegionMeta | None:
    with session_scope() as s:
        r = s.get(Region, region)
        if r is None:
            return None
        n_req = s.scalar(
            select(func.count()).select_from(RequestRow).where(RequestRow.region == region)
        )
        n_eng = s.scalar(
            select(func.count()).select_from(EngineerRow).where(EngineerRow.region == region)
        )
        return RegionMeta(
            id=r.id,
            name=r.name,
            office_address=r.office_address,
            office=LatLng(lat=r.office_lat, lng=r.office_lng, name=f"Офис {r.name}"),
            requests=n_req or 0,
            engineers=n_eng or 0,
        )


def office_for(region: str) -> LatLng:
    with session_scope() as s:
        r = s.get(Region, region)
        if r is None or r.office_lat is None or r.office_lng is None:
            from app.regions import REGIONS

            return REGIONS[region]["office"]
        return LatLng(lat=r.office_lat, lng=r.office_lng, name=f"Офис {r.name}")


def control_metrics(region: str) -> dict | None:
    """Контрольное распределение: что делал реальный диспетчер (справочно).

    Это исторический факт на момент импорта, а не следствие текущего плана,
    поэтому он лежит в regions и не меняется от правок заявок.
    """
    with session_scope() as s:
        r = s.get(Region, region)
        if r is None or not r.control_total:
            return None
        return {
            "total": r.control_total,
            "assigned_count": r.control_assigned,
            "engineers_used": r.control_engineers,
        }


def plan_date(region: str) -> str:
    with session_scope() as s:
        r = s.get(Region, region)
        return (r.plan_date if r else "") or ""


def list_regions() -> list[RegionMeta]:
    with session_scope() as s:
        out = []
        for r in s.scalars(select(Region).order_by(Region.id)):
            n_req = s.scalar(
                select(func.count()).select_from(RequestRow).where(RequestRow.region == r.id)
            )
            n_eng = s.scalar(
                select(func.count()).select_from(EngineerRow).where(EngineerRow.region == r.id)
            )
            out.append(RegionMeta(
                id=r.id, name=r.name, office_address=r.office_address,
                office=LatLng(lat=r.office_lat, lng=r.office_lng),
                requests=n_req or 0, engineers=n_eng or 0,
            ))
        return out


# ---------- Правки и сброс (заход 3) ----------

def _now() -> str:
    # Метку времени считаем в Python, а не через func.strftime: это синтаксис
    # SQLite и на PostgreSQL он бы не прошёл.
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def add_request(region: str, req: Request) -> Request:
    from app.db import _request_kwargs

    with session_scope() as s:
        # Новая заявка приходит позже всех: встаёт в конец порядка поступления.
        last = s.scalar(
            select(func.max(RequestRow.ordinal)).where(RequestRow.region == region)
        )
        s.add(RequestRow(
            **_request_kwargs(req.model_dump()),
            region=region,
            ordinal=(last or -1) + 1,
            origin="manual",
            edited_at=_now(),
        ))
    return req


def _next_manual_id(s, region: str) -> str:
    """Идентификатор ручной заявки.

    Номера из выгрузки — пятизначные, поэтому ручные помечаем префиксом «mk-»:
    так в интерфейсе сразу видно, что заявку добавил диспетчер, и номер не
    случайно не совпадёт с реальным BK-номером.
    """
    n = s.scalar(
        select(func.count()).select_from(RequestRow).where(RequestRow.region == region)
    ) or 0
    while True:
        n += 1
        rid = f"mk-{region}-{n}"
        clash = s.scalar(
            select(func.count()).select_from(RequestRow).where(
                RequestRow.region == region, RequestRow.id == rid
            )
        )
        if not clash:
            return rid


def _norm_window(start: str, end: str) -> tuple[str, str]:
    """Приводит окно «HH:MM» к виду (начало, конец).

    Окно обязательно: обещание времени заказчику — часть предметной области, и
    все солверы (кроме OR-Tools) обращаются к нему без проверки на пустоту.
    Если задан только один край, второй достраивается на 2 часа: молча ставить
    «до конца смены» значило бы придумать ограничение, которого не задавали.
    """
    from app.distance import parse_hhmm, to_hhmm

    start = (start or "").strip()
    end = (end or "").strip()
    if not start and not end:
        raise ValueError("Укажите окно работ: HH:MM, например 09:00-18:00")
    if start and not end:
        end = to_hhmm(min(23 * 60 + 59, parse_hhmm(start) + 120))
    if end and not start:
        start = to_hhmm(max(0, parse_hhmm(end) - 120))
    if parse_hhmm(end) < parse_hhmm(start):
        raise ValueError(f"Конец окна {end} раньше начала {start}")
    return start, end


def create_request(region: str, draft: dict) -> Request:
    """Создаёт заявку из черновика: навык, норматив и срочность выводятся
    из типа заявки, окно нормализуется, координаты берутся из черновика
    (вызывающий код геокодирует адрес, если их не было)."""
    from app.db import _request_kwargs
    from app.regions import urgency_of

    bk_type = draft.get("bk_type") or "Локальная заявка"
    hd_type = draft.get("hd_type") or ""
    window_start, window_end = _norm_window(
        draft.get("window_start", ""), draft.get("window_end", "")
    )
    duration = draft.get("duration_min")
    if duration is None:
        duration = norm_min(bk_type)
    priority = draft.get("priority") or urgency_of(bk_type, hd_type)

    with session_scope() as s:
        payload = {
            "id": _next_manual_id(s, region),
            "bk_type": bk_type,
            "hd_type": hd_type,
            "skill": skill_of(bk_type, hd_type),
            "window_start": window_start,
            "window_end": window_end,
            "district": draft.get("district") or "",
            "address": draft.get("address") or "",
            "tech": draft.get("tech"),
            "gigabit": bool(draft.get("gigabit")),
            "priority": priority,
            "duration_min": int(duration),
            "required_transport": draft.get("required_transport"),
            "status": None,
            "control_brigade": None,
            "lat": draft.get("lat"),
            "lng": draft.get("lng"),
        }
        last = s.scalar(
            select(func.max(RequestRow.ordinal)).where(RequestRow.region == region)
        )
        s.add(RequestRow(
            **_request_kwargs(payload),
            region=region,
            ordinal=(last if last is not None else -1) + 1,
            origin="manual",
            edited_at=_now(),
        ))
    return _to_request_payload(payload)


def _to_request_payload(payload: dict) -> Request:
    payload = dict(payload)
    payload["skill_label"] = SKILLS.get(payload.get("skill"), {}).get("label", "")
    return Request(**payload)


def _find_request(s, region: str, request_id: str):
    """Ищет заявку по паре «регион + номер».

    Именно select с условиями, а не s.get(): у составного ключа позиционный
    get() ждёт значения в порядке объявления колонок, и перестановка молча
    ищет несуществующую строку вместо ошибки.
    """
    return s.scalars(
        select(RequestRow).where(RequestRow.region == region, RequestRow.id == request_id)
    ).first()


def _find_engineer(s, region: str, engineer_id: str):
    return s.scalars(
        select(EngineerRow).where(EngineerRow.region == region, EngineerRow.id == engineer_id)
    ).first()


def get_request(region: str, request_id: str) -> Request | None:
    with session_scope() as s:
        row = _find_request(s, region, request_id)
        return _to_request(row) if row else None


def update_request(region: str, request_id: str, patch: dict) -> Request | None:
    """Частичная правка заявки. patch — только явно заданные поля."""
    from app.distance import parse_hhmm

    with session_scope() as s:
        row = _find_request(s, region, request_id)
        if row is None:
            return None
        before = {c: getattr(row, c) for c in _REQ_COLS}
        for k, v in patch.items():
            setattr(row, k, v)
        # Тип заявки задаёт навык и норматив работ. Меняем «Подключение» на
        # «Локальную» — обязан подтянуться и навык, и норматив, иначе заявка
        # осталась бы со старым. Явно заданные skill/duration_min уважаем.
        type_changed = "bk_type" in patch or "hd_type" in patch
        if type_changed and "skill" not in patch:
            row.skill = skill_of(row.bk_type, row.hd_type)
        if type_changed and "duration_min" not in patch:
            row.duration_min = norm_min(row.bk_type)
        if row.window_start and row.window_end:
            if parse_hhmm(row.window_end) < parse_hhmm(row.window_start):
                raise ValueError(
                    f"Конец окна {row.window_end} раньше начала {row.window_start}"
                )
        elif row.window_start or row.window_end:
            # Солверы читают обе границы без проверки на пустоту, поэтому окно
            # обязано быть цельным — иначе план упадёт на 500.
            raise ValueError("Укажите обе границы окна: начало и конец")
        if {c: getattr(row, c) for c in _REQ_COLS} != before:
            row.edited_at = _now()
        out = _to_request(row)
    return out


def get_engineer(region: str, engineer_id: str) -> Engineer | None:
    with session_scope() as s:
        row = _find_engineer(s, region, engineer_id)
        return _to_engineer(row) if row else None


def update_engineer(region: str, engineer_id: str, patch: dict) -> Engineer | None:
    """Частичная правка инженера. Навыки и подписи — через справочники."""
    with session_scope() as s:
        row = _find_engineer(s, region, engineer_id)
        if row is None:
            return None
        before = {c: getattr(row, c) for c in _ENG_COLS}
        for k, v in patch.items():
            setattr(row, k, v)
        if {c: getattr(row, c) for c in _ENG_COLS} != before:
            row.edited_at = _now()
        out = _to_engineer(row)
    return out


def delete_request(region: str, request_id: str) -> bool:
    with session_scope() as s:
        n = s.execute(
            delete(RequestRow).where(RequestRow.region == region, RequestRow.id == request_id)
        ).rowcount
    return bool(n)


def reset_region(s, region: str, requests: bool, engineers: bool) -> dict:
    """Сброс региона к импортированному состоянию (галочки выбирает вызывающий).

    Выбираем именно select(Таблица) — при select(ORM-сущности) .mappings()
    отдаёт словарь с ключом-названием сущности, а не с колонками, и вставка
    уходит в базу пустыми значениями по умолчанию.
    """
    out: dict[str, int] = {}
    if requests:
        s.execute(delete(RequestRow).where(RequestRow.region == region))
        base = s.execute(
            select(RequestBaseline.__table__).where(RequestBaseline.region == region)
        ).mappings().all()
        if base:
            s.execute(
                RequestRow.__table__.insert(),
                [dict(r, origin="baseline", edited_at=None) for r in base],
            )
        out["requests"] = len(base)
    if engineers:
        s.execute(delete(EngineerRow).where(EngineerRow.region == region))
        base = s.execute(
            select(EngineerBaseline.__table__).where(EngineerBaseline.region == region)
        ).mappings().all()
        if base:
            s.execute(
                EngineerRow.__table__.insert(),
                [dict(r, origin="baseline", edited_at=None) for r in base],
            )
        out["engineers"] = len(base)
    return out


def edit_status(region: str) -> dict:
    """Сколько заявок добавлено, изменено и удалено относительно импорта."""
    with session_scope() as s:
        added = s.scalar(
            select(func.count()).select_from(RequestRow)
            .where(RequestRow.region == region, RequestRow.origin == "manual")
        ) or 0
        edited = s.scalar(
            select(func.count()).select_from(RequestRow)
            .where(RequestRow.region == region, RequestRow.edited_at.is_not(None))
        ) or 0
        n_now = s.scalar(
            select(func.count()).select_from(RequestRow).where(RequestRow.region == region)
        ) or 0
        n_base = s.scalar(
            select(func.count()).select_from(RequestBaseline).where(RequestBaseline.region == region)
        ) or 0
        e_added = s.scalar(
            select(func.count()).select_from(EngineerRow)
            .where(EngineerRow.region == region, EngineerRow.origin == "manual")
        ) or 0
        e_edited = s.scalar(
            select(func.count()).select_from(EngineerRow)
            .where(EngineerRow.region == region, EngineerRow.edited_at.is_not(None))
        ) or 0
        e_now = s.scalar(
            select(func.count()).select_from(EngineerRow).where(EngineerRow.region == region)
        ) or 0
        e_base = s.scalar(
            select(func.count()).select_from(EngineerBaseline).where(EngineerBaseline.region == region)
        ) or 0
    return {
        "region": region,
        "requests": {
            "added": added,
            "edited": edited,
            "deleted": max(0, n_base - (n_now - added)),
            "total": n_now,
        },
        "engineers": {
            "added": e_added,
            "edited": e_edited,
            "deleted": max(0, e_base - (e_now - e_added)),
            "total": e_now,
        },
    }
