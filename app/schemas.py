"""Контракт API — единая JSON-схема для фронта и солверов.

Любой солвер (наш / тиммейта / OR-Tools) обязан вернуть PlanResponse в этом виде.
"""

from typing import Literal, Optional

from pydantic import BaseModel, Field


class LatLng(BaseModel):
    lat: float
    lng: float
    name: str = ""


class Request(BaseModel):
    id: str
    bk_type: str
    hd_type: str = ""
    skill: str                                # ключ навыка
    skill_label: str
    window_start: str = ""                    # "HH:MM" — начало обещанного интервала
    window_end: str = ""                      # "HH:MM"
    district: str = ""
    address: str
    tech: Optional[str] = None                # FMC / FTTB / None
    gigabit: bool = False
    priority: str = "normal"                  # normal | urgent (аварийная — раньше)
    duration_min: int = 0                     # норматив работ (без дороги)
    required_transport: Optional[str] = None  # auto | None — ресурсное требование
    status: Optional[str] = None              # статус BK из источника (remote: Отправлена/…) — справочно
    control_brigade: Optional[str] = None     # бригада контрольного распределения (справочно)
    lat: Optional[float] = None
    lng: Optional[float] = None


class RoadStop(BaseModel):
    step: int
    request_id: str
    address: str = ""
    lat: float
    lng: float
    arrival: str                              # HH:MM — прибытие инженера
    start_work: str                           # HH:MM — начало работ (>= окна)
    finish: str                               # HH:MM — конец работ
    window: list[str] = []                    # [start, end]
    duration_min: int = 0


class Engineer(BaseModel):
    id: str
    name: str
    skills: list[str] = []
    skills_label: list[str] = []
    transport: str = ""                       # auto | transit | bike | walk
    transport_label: str = ""
    speed_kph: Optional[float] = None         # прямая скорость (remote-модель коллеги); иначе из transport
    shift_start: str = "08:00"
    shift_end: str = "20:00"
    start: LatLng = Field(default_factory=LatLng)
    route: Optional[list[list[float]]] = None  # полилиния по дорогам [[lat,lng],…]; None = рисовать прямую
    stops: list[RoadStop] = []
    km: float = 0.0
    minutes: int = 0


class UnassignedReason(BaseModel):
    request_id: str
    address: str = ""
    reason: str
    window: list[str] = []
    skill_label: str = ""


class PlanMetrics(BaseModel):
    total_requests: int = 0
    assigned_count: int = 0
    unassigned_count: int = 0
    engineers_used: int = 0
    total_km: float = 0.0
    total_minutes: int = 0


class Comparison(BaseModel):
    """Сравнение improved vs baseline (+ контрольное распределение справочно)."""
    baseline: Optional[PlanMetrics] = None
    improved: Optional[PlanMetrics] = None
    control: Optional[dict] = None


class PlanResponse(BaseModel):
    region: str
    region_name: str = ""
    mode: str = ""
    date: str = ""
    dist: str = ""
    engineers: list[Engineer] = []
    unassigned: list[UnassignedReason] = []
    metrics: PlanMetrics = Field(default_factory=PlanMetrics)
    comparison: Comparison = Field(default_factory=Comparison)


class ScenarioEvent(BaseModel):
    # urgent — новая срочная заявка; cancel — отмена заявки; unavailable — инженер недоступен
    type: Literal["urgent", "cancel", "unavailable"]
    request_id: Optional[str] = None
    engineer_id: Optional[str] = None


class ScenarioResult(BaseModel):
    event: str
    plan: PlanResponse
    diff: list[dict] = []                     # человеко-читаемые изменения плана


class RegionMeta(BaseModel):
    id: str
    name: str
    office_address: str
    office: Optional[LatLng] = None
    requests: int = 0
    engineers: int = 0


SolverMode = Literal["baseline_fifo", "improved", "benchmark_ortools"]
DistMode = Literal["haversine", "osrm"]
DataSource = Literal["mock", "csv", "remote", "db"]
RegionId = Literal["vostok", "yugo_vostok", "yugocentr"]


class SettingsIn(BaseModel):
    solver_mode: Optional[SolverMode] = None
    dist_mode: Optional[DistMode] = None
    data_source: Optional[DataSource] = None
    region: Optional[RegionId] = None


class SettingsOut(BaseModel):
    solver_mode: SolverMode = "improved"
    dist_mode: DistMode = "haversine"
    data_source: DataSource = "csv"
    region: RegionId = "vostok"