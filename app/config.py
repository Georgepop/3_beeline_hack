from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    app_name: str = "Beeline Field Service"
    # Инфраструктура: SQLAlchemy-совместимый URL (SQLite по умолчанию, PG — сменой DATABASE_URL).
    database_url: str = "sqlite:///./data/app.db"

    # Логика (переключаются без правки кода: параметры запроса / /api/settings / env):
    solver_mode: str = "improved"      # baseline_fifo | improved | benchmark_ortools
    dist_mode: str = "haversine"       # haversine | osrm
    geocoder: str = "nominatim"        # nominatim | districts
    region: str = "vostok"

    osrm_url: str = "https://router.project-osrm.org"
    osrm_enabled: bool = True             # полилинии по дорогам для карты; False = прямые линии
    # Геометрия грузится отдельным запросом после плана, поэтому ждать дольше
    # 4 с бессмысленно: фронт и так рисует прямую линию.
    osrm_timeout: int = 4
    nominatim_url: str = "https://nominatim.openstreetmap.org"

    shift_start: str = "08:00"
    shift_end: str = "20:00"

    # Бенчмарк OR-Tools (requirements-benchmark.txt). Цель — минуты: плата за
    # каждого реально задействованного инженера + штраф за пропуск заявки.
    # Лимит поиска. Замерено на всех трёх регионах по 3 прогона: 5 с и 10 с дают
    # одинаковый результат (Восток 57, Юго-центр 46, Юго-восток 59), а 30 с
    # добавляет ровно одну заявку на Юго-востоке — и только в последние ~5 с
    # поиска, то есть это финальное улучшение GLS у самого дедлайна, а не
    # устойчивый выигрыш. Одна заявка из 83 не окупает 30 с ожидания, поэтому
    # потолок 5 с; с кешем плана (app/session.py) он платится один раз.
    ortools_time_limit: int = 5
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