"""Подключение к БД и первичный импорт.

Хранилище одно: SQLite по умолчанию, PostgreSQL — сменой DATABASE_URL. Схема
создаётся create_all() в app/models.py; Alembic для прототипа не нужен, DDL в
одном месте.

Почему сессия на вызов, а не одна на приложение: эндпоинты в app/api.py
синхронные, FastAPI выполняет их в пуле потоков, а Session не потокобезопасен.
Общая сессия давала бы редкие невоспроизводимые падения.

CLI:  python -m app.db import [--force] [--region vostok]
"""

import argparse
import json
import sys
from contextlib import contextmanager
from pathlib import Path

from sqlalchemy import create_engine, delete, event, func, select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from app.config import get_settings
from app.models import (
    Base,
    EngineerBaseline,
    EngineerRow,
    Region,
    RequestBaseline,
    RequestRow,
)

_engine: Engine | None = None
_session_factory: sessionmaker | None = None

# Статусы, которые не планируются: заявка показывается на карте, но не входит
# в план. Правило было заведено для данных коллеги, откуда приходили статусы.
DONE_STATUSES = {"Отменена", "Выполнена"}


def _make_engine(url: str) -> Engine:
    kwargs: dict = {"future": True}
    if url.startswith("sqlite"):
        # Файл SQLite: пул не держим — каждая сессия получает своё соединение,
        # иначе потоки пула FastAPI делят один conn.
        from sqlalchemy.pool import NullPool

        kwargs["poolclass"] = NullPool
        kwargs["connect_args"] = {"check_same_thread": False}
    eng = create_engine(url, **kwargs)
    if url.startswith("sqlite"):
        @event.listens_for(eng, "connect")
        def _pragmas(dbapi_conn, _rec):  # noqa: ANN001
            # WAL: читатели не блокируют писателя. Пригодится, когда диспетчер
            # правит заявку, пока воркеры досчитывают предыдущий план.
            cur = dbapi_conn.cursor()
            cur.execute("PRAGMA journal_mode=WAL")
            cur.execute("PRAGMA synchronous=NORMAL")
            cur.close()
    return eng


def get_engine() -> Engine:
    global _engine, _session_factory
    if _engine is None:
        url = get_settings().database_url
        if url.startswith("sqlite:///"):
            # sqlite:///./data/app.db — каталог мог не существовать
            p = Path(url.replace("sqlite:///", "", 1))
            if p.parent and str(p.parent) not in ("", "."):
                p.parent.mkdir(parents=True, exist_ok=True)
        _engine = _make_engine(url)
        _session_factory = sessionmaker(bind=_engine, expire_on_commit=False)
    return _engine


@contextmanager
def session_scope() -> Session:
    """Сессия на одну операцию: коммит на успех, откат на исключение."""
    if _session_factory is None:
        get_engine()
    assert _session_factory is not None
    s = _session_factory()
    try:
        yield s
        s.commit()
    except Exception:
        s.rollback()
        raise
    finally:
        s.close()


def create_schema() -> None:
    Base.metadata.create_all(get_engine())


# ---------- Импорт ----------

def _is_active(status: str | None) -> bool:
    return (status or "").strip() not in DONE_STATUSES


def _request_kwargs(r: dict) -> dict:
    """Словарь всех колонок RequestRow/RequestBaseline из pydantic-модели.

    skill_label и skills_label не храним: это подписи навыков, они выводятся
    из справочника app/regions.py и не несут данных.
    """
    return {
        "id": r["id"],
        "is_active": _is_active(r.get("status")),
        "bk_type": r.get("bk_type") or "",
        "hd_type": r.get("hd_type") or "",
        "skill": r.get("skill") or "",
        "window_start": r.get("window_start") or "",
        "window_end": r.get("window_end") or "",
        "district": r.get("district") or "",
        "address": r.get("address") or "",
        "tech": r.get("tech"),
        "gigabit": bool(r.get("gigabit")),
        "priority": r.get("priority") or "normal",
        "duration_min": int(r.get("duration_min") or 0),
        "required_transport": r.get("required_transport"),
        "status": r.get("status"),
        "control_brigade": r.get("control_brigade"),
        "lat": r.get("lat"),
        "lng": r.get("lng"),
    }


def _engineer_kwargs(e: dict, region: str) -> dict:
    start = e.get("start") or {}
    return {
        "id": e["id"],
        "region": region,
        "name": e.get("name") or "",
        "skills": list(e.get("skills") or []),
        "transport": e.get("transport") or "",
        "speed_kph": e.get("speed_kph"),
        "shift_start": e.get("shift_start") or "08:00",
        "shift_end": e.get("shift_end") or "20:00",
        "lat": start.get("lat"),
        "lng": start.get("lng"),
    }


def import_region(s: Session, region: str, parsed: dict, control: dict | None) -> dict:
    """Заполняет рабочие таблицы и baseline-копии для одного региона.

    Переимпорт полностью заменяет регион: иначе старая запись, удалённая из
    CSV, осталась бы жить в базе и тихо влияла бы на план.
    """
    for table in (RequestRow, EngineerRow, RequestBaseline, EngineerBaseline):
        s.execute(delete(table).where(table.region == region))
    s.execute(delete(Region).where(Region.id == region))

    office = parsed.get("office") or {}
    ctrl = control or {}
    s.add(Region(
        id=region,
        name=parsed.get("region_name") or region,
        office_address=parsed.get("office_address") or "",
        office_lat=office.get("lat"),
        office_lng=office.get("lng"),
        plan_date=parsed.get("date") or "",
        control_total=int(ctrl.get("total") or 0),
        control_assigned=int(ctrl.get("assigned_count") or 0),
        control_engineers=int(ctrl.get("engineers_used") or 0),
        imported_from="csv",
    ))

    n_req = n_eng = 0
    for i, r in enumerate(parsed.get("requests") or []):
        kw = _request_kwargs(r)
        s.add(RequestRow(**kw, region=region, ordinal=i, origin="baseline", edited_at=None))
        s.add(RequestBaseline(**kw, region=region, ordinal=i))
        n_req += 1
    for i, e in enumerate(parsed.get("engineers") or []):
        kw = _engineer_kwargs(e, region)
        s.add(EngineerRow(**kw, ordinal=i, origin="baseline", edited_at=None))
        # ordinal обязателен и в baseline: сброс копирует инженеров отсюда, и
        # без номера порядок бригад после сброса поехал бы (1, 10, 11, 12, 2…).
        s.add(EngineerBaseline(**kw, ordinal=i))
        n_eng += 1
    return {"region": region, "requests": n_req, "engineers": n_eng}


def import_all(force: bool = False, only: str | None = None) -> list[dict]:
    """Импорт из CSV. Если БД уже наполнена и force не задан — ничего не делает."""
    from app import data_source
    from app.regions import REGIONS

    create_schema()
    regions = [only] if only else list(REGIONS)
    out: list[dict] = []
    with session_scope() as s:
        for region in regions:
            if not force:
                already = s.scalar(
                    select(func.count()).select_from(RequestRow).where(RequestRow.region == region)
                )
                if already:
                    out.append({"region": region, "skipped": "уже импортирован"})
                    continue
            parsed = data_source.load_region(region)   # распарсенный JSON, иначе сборка из CSV
            control = data_source.control_metrics(region)
            out.append(import_region(s, region, parsed, control))
    return out


def is_empty() -> bool:
    """Пустая ли БД — нужно ли автоимпортировать при старте."""
    try:
        create_schema()
        with session_scope() as s:
            n = s.scalar(select(func.count()).select_from(RequestRow))
            return not n
    except Exception:
        return False


def ensure_initialized() -> None:
    """Вызывается при создании приложения: БД готова к работе всегда."""
    create_schema()
    if is_empty():
        import_all()


def reset(s: Session) -> None:
    """Полный сброс всех регионов к импортированному состоянию.

    Логика сброса живёт одна — в repository.reset_region. Две копии этого кода
    уже разъехались: одна брала baseline через select(ORM-сущность), и
    .mappings() отдавала ключ-название сущности, из-за чего в базу уходили
    пустые строки без номера заявки.
    """
    from app.models import Region
    from app.repository import reset_region

    for (region,) in s.execute(select(Region.id)).all():
        reset_region(s, region, True, True)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Импорт данных в БД")
    ap.add_argument("cmd", nargs="?", default="import", choices=["import", "reset", "status"])
    ap.add_argument("--force", action="store_true", help="перезаписать уже импортированные регионы")
    ap.add_argument("--region", default=None)
    a = ap.parse_args(argv)

    if a.cmd == "import":
        for row in import_all(force=a.force, only=a.region):
            print(json.dumps(row, ensure_ascii=False))
    elif a.cmd == "reset":
        with session_scope() as s:
            reset(s)
        print("БД сброшена к импортированным данным")
    else:
        with session_scope() as s:
            for r in s.scalars(select(Region)):
                nq = s.scalar(select(func.count()).select_from(RequestRow).where(RequestRow.region == r.id))
                ne = s.scalar(select(func.count()).select_from(EngineerRow).where(EngineerRow.region == r.id))
                print(f"{r.id:12} заявок={nq:4} инженеров={ne:3} контроль={r.control_assigned}/{r.control_total} в {r.control_engineers} бригад")
    return 0


if __name__ == "__main__":
    sys.exit(main())
