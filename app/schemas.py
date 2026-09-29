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
    reason_code: str = ""      # no_skill | no_transport | outside_shift | no_capacity | no_coords | …
    reason_detail: str = ""    # что именно не сработало: время, число исполнителей, окно
    window: list[str] = []
    skill_label: str = ""


class PlanMetrics(BaseModel):
    total_requests: int = 0
    assigned_count: int = 0
    unassigned_count: int = 0
    engineers_used: int = 0
    total_km: float = 0.0
    total_minutes: int = 0


class PlanSummary(BaseModel):
    """Краткое объяснение результата: сколько могла смена и что помешало (ТЗ 2.4.2)."""
    headline: str = ""                        # итог одной фразой
    text: str = ""                           # что стоит на пути к пределу смены
    assigned: int = 0
    total: int = 0
    ceiling: int = 0                         # предел смены по жёстким окнам
    engineers_used: int = 0
    total_km: float = 0.0
    total_minutes: int = 0
    factors: list[str] = []                  # что встало на пути, по частоте причин
    reason_counts: dict[str, int] = {}       # коды причин -> количество


class Comparison(BaseModel):
    """Сравнение текущего плана с базовым FIFO (+ контрольное распределение справочно)."""
    baseline: Optional[PlanMetrics] = None
    improved: Optional[PlanMetrics] = None
    control: Optional[dict] = None
    baseline_mode: str = ""       # какой режим в baseline (baseline_fifo)
    improved_mode: str = ""       # какой режим сравниваем (improved / benchmark_ortools)
    note: str = ""                # одна фраза: что показало сравнение


class PlanResponse(BaseModel):
    region: str
    region_name: str = ""
    mode: str = ""
    date: str = ""
    dist: str = ""
    engineers: list[Engineer] = []
    unassigned: list[UnassignedReason] = []
    metrics: PlanMetrics = Field(default_factory=PlanMetrics)
    summary: Optional[PlanSummary] = None
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


class RequestExplanation(BaseModel):
    """Объяснение по одной заявке: почему назначена и почему именно так (ТЗ 2.1.7).

    Считается по фактическому плану, а не пишется текстом: альтернативы
    проверяются тем же расчётом маршрута, что и сам план.
    """
    request_id: str
    address: str = ""
    assigned: bool = False
    headline: str = ""                        # главная фраза для карточки
    engineer: str = ""
    facts: list[str] = []                     # проверенные ограничения и числа
    alternatives: list[dict] = []             # кто ещё мог взять и с каким пробегом
    rejected: list[dict] = []                 # кто не подошёл и почему
    reason_code: str = ""
    reason: str = ""
    detail: str = ""
    hint: str = ""                            # что помогло бы выполнить заявку


class EngineerRoute(BaseModel):
    """Полилиния маршрута одного инженера для отрисовки (не для расчёта)."""
    engineer_id: str
    route: Optional[list[list[float]]] = None  # [[lat, lng], ...]; None = рисовать прямой


class GeometryResponse(BaseModel):
    """Ответ GET /api/plan/geometry: геометрия приезжает отдельным запросом,
    уже после того как план посчитан, поэтому сеть не держит расчёт."""
    region: str
    mode: str
    routes: list[EngineerRoute] = []


class RegionMeta(BaseModel):
    id: str
    name: str
    office_address: str
    office: Optional[LatLng] = None
    requests: int = 0
    engineers: int = 0


# --- Правка данных в БД (заход 3) ---

class RequestDraft(BaseModel):
    """Новая заявка.

    Обязательны адрес и окно работ. Навык, норматив работ и срочность выводятся
    из типа заявки по справочникам, ровно как при импорте CSV. Координаты
    необязательны: если их нет, адрес геокодируется на сервере.
    """
    address: str
    bk_type: str = "Локальная заявка"
    hd_type: str = ""
    district: str = ""
    window_start: str = ""
    window_end: str = ""
    tech: Optional[str] = None
    gigabit: bool = False
    priority: Optional[str] = None        # None = вывести по типу заявки
    duration_min: Optional[int] = None    # None = норматив по типу заявки
    required_transport: Optional[str] = None
    lat: Optional[float] = None
    lng: Optional[float] = None


class RequestPatch(BaseModel):
    """Частичная правка заявки: unset-поля не трогаются.

    is_active=False равносильно отмене — заявка остаётся в базе и на карте, но
    не попадает в план (так же работают статусы в исходных данных).
    """
    address: Optional[str] = None
    bk_type: Optional[str] = None
    hd_type: Optional[str] = None
    district: Optional[str] = None
    window_start: Optional[str] = None
    window_end: Optional[str] = None
    tech: Optional[str] = None
    gigabit: Optional[bool] = None
    priority: Optional[str] = None
    duration_min: Optional[int] = None
    required_transport: Optional[str] = None
    lat: Optional[float] = None
    lng: Optional[float] = None
    is_active: Optional[bool] = None


class EngineerPatch(BaseModel):
    """Правка инженера: смена, транспорт, навыки, скорость."""
    name: Optional[str] = None
    skills: Optional[list[str]] = None
    transport: Optional[str] = None
    speed_kph: Optional[float] = None
    shift_start: Optional[str] = None
    shift_end: Optional[str] = None


class ResetIn(BaseModel):
    """Сброс к импортированному состоянию. Галочки выбирает пользователь,
    по умолчанию сбрасываются и заявки, и инженеры."""
    requests: bool = True
    engineers: bool = True


class EditCounts(BaseModel):
    added: int = 0
    edited: int = 0
    deleted: int = 0
    total: int = 0


class DbStatus(BaseModel):
    """Что изменилось относительно импорта — этим же считаем, можно ли сбрасывать."""
    region: str
    region_name: str = ""
    requests: EditCounts
    engineers: EditCounts
    dirty: bool = False


class GeoReverseIn(BaseModel):
    lat: float
    lng: float


class GeoReverseOut(BaseModel):
    """Обратное геокодирование клика по карте.

    Точного адреса у координаты нет — возвращаем ближайший (обычно это соседний
    дом), поэтому поле ok=False означает «не нашли, введите адрес вручную»,
    а не ошибку сервера.
    """
    ok: bool
    address: str = ""
    district: str = ""
    lat: Optional[float] = None
    lng: Optional[float] = None


SolverMode = Literal["baseline_fifo", "improved", "benchmark_ortools"]
DistMode = Literal["haversine", "osrm"]
RegionId = Literal["vostok", "yugo_vostok", "yugocentr"]


class SettingsIn(BaseModel):
    solver_mode: Optional[SolverMode] = None
    dist_mode: Optional[DistMode] = None
    region: Optional[RegionId] = None


class SettingsOut(BaseModel):
    solver_mode: SolverMode = "improved"
    dist_mode: DistMode = "haversine"
    region: RegionId = "vostok"