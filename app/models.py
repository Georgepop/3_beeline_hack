"""Таблицы SQLAlchemy 2.0.

Перенос на PostgreSQL — сменой DATABASE_URL (плюс драйвер psycopg). Поэтому
здесь только переносимые типы SQLAlchemy: никаких INSERT OR REPLACE, sqlite3
и сырых строк в SQL.

Особенность: рядом с рабочими таблицами requests/engineers лежат их
baseline-копии — нетронутые снимки на момент импорта. Сброс к исходным данным
это DELETE + INSERT ... SELECT из них, поэтому он всегда точен, даже если
правили исходную строку, а не только добавляли новую.

origin ('baseline' | 'manual') и edited_at (NULL, если строку не трогали) —
единственные служебные поля: по ним интерфейс показывает ручные правки.
"""

from sqlalchemy import Boolean, Float, Integer, JSON, String, Text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class Region(Base):
    """Регион: офис, дата плана и метрики контрольного распределения.

    Контрольные метрики хранятся по региону, а не по заявкам: это исторический
    факт («что сделал реальный диспетчер»), и он не должен меняться, когда
    диспетчер правит заявки. Связать бригаду с заявкой по ID нельзя — в
    синтетическом и контрольном CSV общих идентификаторов нет.
    """

    __tablename__ = "regions"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    name: Mapped[str] = mapped_column(String(128), default="")
    office_address: Mapped[str] = mapped_column(Text, default="")
    office_lat: Mapped[float | None] = mapped_column(Float, nullable=True)
    office_lng: Mapped[float | None] = mapped_column(Float, nullable=True)
    plan_date: Mapped[str] = mapped_column(String(32), default="")
    control_total: Mapped[int] = mapped_column(Integer, default=0)
    control_assigned: Mapped[int] = mapped_column(Integer, default=0)
    control_engineers: Mapped[int] = mapped_column(Integer, default=0)
    # Откуда импортировано — для диагностики и честной подписи в README.
    imported_from: Mapped[str] = mapped_column(String(32), default="csv")


class RequestRow(Base):
    """Заявка в рабочей таблице."""

    __tablename__ = "requests"

    # Ключ составной: заявка опознаётся парой «регион + номер», а не номером
    # самим по себе. Номера приходят из разных выгрузок, и одинаковый номер в
    # двух регионах — это две разные заявки, а не конфликт ключа.
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    region: Mapped[str] = mapped_column(String(32), primary_key=True)
    # Порядок поступления из файла. Обязателен: солверы зависят от порядка
    # (ТЗ — «заявки обрабатываются по порядку поступления»), а сортировка по id
    # его ломает: идентификаторы строковые, и 'eng-10' встаёт раньше 'eng-2'.
    ordinal: Mapped[int] = mapped_column(Integer, default=0)
    # is_active отдельно от status: у части источников заявки показываются на
    # карте, но не планируются. Считается при импорте, дальше это фильтр в SQL.
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    bk_type: Mapped[str] = mapped_column(String(128), default="")
    hd_type: Mapped[str] = mapped_column(String(128), default="")
    skill: Mapped[str] = mapped_column(String(32), default="")
    window_start: Mapped[str] = mapped_column(String(8), default="")
    window_end: Mapped[str] = mapped_column(String(8), default="")
    district: Mapped[str] = mapped_column(String(128), default="")
    address: Mapped[str] = mapped_column(Text, default="")
    tech: Mapped[str | None] = mapped_column(String(32), nullable=True)
    gigabit: Mapped[bool] = mapped_column(Boolean, default=False)
    priority: Mapped[str] = mapped_column(String(16), default="normal")
    duration_min: Mapped[int] = mapped_column(Integer, default=0)
    required_transport: Mapped[str | None] = mapped_column(String(32), nullable=True)
    status: Mapped[str | None] = mapped_column(String(64), nullable=True)
    control_brigade: Mapped[str | None] = mapped_column(String(128), nullable=True)
    lat: Mapped[float | None] = mapped_column(Float, nullable=True)
    lng: Mapped[float | None] = mapped_column(Float, nullable=True)
    origin: Mapped[str] = mapped_column(String(16), default="baseline")
    edited_at: Mapped[str | None] = mapped_column(String(32), nullable=True)


class EngineerRow(Base):
    """Инженер в рабочей таблице."""

    __tablename__ = "engineers"

    # Составной ключ по той же причине, что и в requests: инженер принадлежит
    # региону, и «id инженера» имеет смысл только внутри него.
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    region: Mapped[str] = mapped_column(String(32), primary_key=True)
    name: Mapped[str] = mapped_column(String(128), default="")
    # JSON, а не отдельная таблица навыков: список из 1-3 строк, а портируемый
    # sqlalchemy.JSON одинаково работает на SQLite (TEXT) и PostgreSQL (JSONB).
    skills: Mapped[list] = mapped_column(JSON, default=list)
    transport: Mapped[str] = mapped_column(String(32), default="")
    speed_kph: Mapped[float | None] = mapped_column(Float, nullable=True)
    shift_start: Mapped[str] = mapped_column(String(8), default="08:00")
    shift_end: Mapped[str] = mapped_column(String(8), default="20:00")
    lat: Mapped[float | None] = mapped_column(Float, nullable=True)
    lng: Mapped[float | None] = mapped_column(Float, nullable=True)
    # См. ordinal в RequestRow: без него порядок инженеров тоже поедет.
    ordinal: Mapped[int] = mapped_column(Integer, default=0)
    origin: Mapped[str] = mapped_column(String(16), default="baseline")
    edited_at: Mapped[str | None] = mapped_column(String(32), nullable=True)


class RequestBaseline(Base):
    """Нетронутый снимок заявок на момент импорта — источник для сброса."""

    __tablename__ = "requests_baseline"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    region: Mapped[str] = mapped_column(String(32), primary_key=True)
    ordinal: Mapped[int] = mapped_column(Integer, default=0)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    bk_type: Mapped[str] = mapped_column(String(128), default="")
    hd_type: Mapped[str] = mapped_column(String(128), default="")
    skill: Mapped[str] = mapped_column(String(32), default="")
    window_start: Mapped[str] = mapped_column(String(8), default="")
    window_end: Mapped[str] = mapped_column(String(8), default="")
    district: Mapped[str] = mapped_column(String(128), default="")
    address: Mapped[str] = mapped_column(Text, default="")
    tech: Mapped[str | None] = mapped_column(String(32), nullable=True)
    gigabit: Mapped[bool] = mapped_column(Boolean, default=False)
    priority: Mapped[str] = mapped_column(String(16), default="normal")
    duration_min: Mapped[int] = mapped_column(Integer, default=0)
    required_transport: Mapped[str | None] = mapped_column(String(32), nullable=True)
    status: Mapped[str | None] = mapped_column(String(64), nullable=True)
    control_brigade: Mapped[str | None] = mapped_column(String(128), nullable=True)
    lat: Mapped[float | None] = mapped_column(Float, nullable=True)
    lng: Mapped[float | None] = mapped_column(Float, nullable=True)


class EngineerBaseline(Base):
    """Нетронутый снимок инженеров на момент импорта — источник для сброса."""

    __tablename__ = "engineers_baseline"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    region: Mapped[str] = mapped_column(String(32), primary_key=True)
    name: Mapped[str] = mapped_column(String(128), default="")
    skills: Mapped[list] = mapped_column(JSON, default=list)
    transport: Mapped[str] = mapped_column(String(32), default="")
    speed_kph: Mapped[float | None] = mapped_column(Float, nullable=True)
    shift_start: Mapped[str] = mapped_column(String(8), default="08:00")
    shift_end: Mapped[str] = mapped_column(String(8), default="20:00")
    lat: Mapped[float | None] = mapped_column(Float, nullable=True)
    lng: Mapped[float | None] = mapped_column(Float, nullable=True)
    ordinal: Mapped[int] = mapped_column(Integer, default=0)
