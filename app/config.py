from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    app_name: str = "Beeline Field Service"
    # Инфраструктура: SQLAlchemy-совместимый URL (SQLite по умолчанию, PG — сменой DATABASE_URL).
    database_url: str = "sqlite:///./data/app.db"

    # Логика (переключаются без правки кода: параметры запроса / /api/settings / env):
    data_source: str = "csv"           # mock | csv | remote | db
    solver_mode: str = "improved"      # baseline_fifo | improved | benchmark_ortools
    dist_mode: str = "haversine"       # haversine | osrm
    geocoder: str = "nominatim"        # nominatim | districts
    region: str = "vostok"

    osrm_url: str = "https://router.project-osrm.org"
    osrm_enabled: bool = True             # полилинии по дорогам для карты; False = прямые линии
    osrm_timeout: int = 15
    nominatim_url: str = "https://nominatim.openstreetmap.org"
    # API коллеги (данные по точкам и модели инженеров) — источник data_source=remote.
    remote_base_url: str = "http://85.198.65.126:8000"
    remote_timeout: int = 20

    shift_start: str = "08:00"
    shift_end: str = "20:00"

    # Бенчмарк OR-Tools (requirements-benchmark.txt). Цель — минуты: плата за
    # каждого реально задействованного инженера + штраф за пропуск заявки.
    ortools_time_limit: int = 30        # лимит поиска, сек (10 не хватает на Юго-востоке)
    ortools_drop_penalty: int = 100000  # штраф за невыполненную заявку (избыточно большой)
    # Срочная заявка важнее обычной, поэтому за её пропуск штраф выше (ТЗ 2.2).
    # Множитель, а не новое число: правка базового штрафа не разъезжается с ним.
    ortools_urgent_penalty_factor: int = 3  # 100000 -> 300000 для priority=urgent
    ortools_fixed_vehicle_cost: int = 45  # плата за инженера в минутах
    ortools_slack_max: int = 1440        # максимум ожидания до начала окна, мин
    # Стратегии поиска (имя поля в routing_enums_pb2, регистр не важен)
    ortools_first_solution: str = "parallel_cheapest_insertion"
    ortools_local_search: str = "guided_local_search"

    # Средние скорости по типу транспорта, км/ч (допущение, README).
    speed_kmh: dict[str, float] = {
        "auto": 30.0,
        "transit": 20.0,
        "bike": 15.0,
        "walk": 5.0,
    }
    # Дорожный коэффициент для haversine (поправка на непрямую езду), README.
    road_factor: float = 1.3


@lru_cache
def get_settings() -> Settings:
    return Settings()