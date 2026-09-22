from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    app_name: str = "Beeline Field Service"
    # Инфраструктура: SQLAlchemy-совместимый URL (SQLite по умолчанию, PG — сменой DATABASE_URL).
    database_url: str = "sqlite:///./data/app.db"

    # Логика (переключаются без правки кода: параметры запроса / /api/settings / env):
    data_source: str = "csv"           # mock | csv | db
    solver_mode: str = "improved"      # baseline_fifo | improved | benchmark_ortools
    dist_mode: str = "haversine"       # haversine | osrm
    geocoder: str = "nominatim"        # nominatim | districts
    region: str = "vostok"

    osrm_url: str = "https://router.project-osrm.org"
    nominatim_url: str = "https://nominatim.openstreetmap.org"

    shift_start: str = "08:00"
    shift_end: str = "20:00"

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