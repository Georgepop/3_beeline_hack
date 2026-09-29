"""API HTTP-слой (FastAPI). Контракт из app/schemas.py."""

from pathlib import Path

from fastapi import Body, FastAPI, HTTPException, Query

from app import explain as explain_mod
from app import mock
from app import planner
from app import session
from app.config import get_settings
from app.regions import REGIONS
from app.schemas import (
    DistMode,
    Engineer,
    PlanResponse,
    RegionId,
    RegionMeta,
    Request,
    RequestExplanation,
    ScenarioEvent,
    ScenarioResult,
    SettingsIn,
    SettingsOut,
    SolverMode,
)

# Человекочитаемые названия алгоритмов (для селектора во фронте, GET /api/solvers).
SOLVER_LABELS = {
    "baseline_fifo": "Базовый (FIFO)",
    "improved": "Улучшенный",
    "benchmark_ortools": "OR-Tools (бенчмарк)",
}

# Хранимые настройки (пока in-memory; Phase 5 — таблица settings в БД).
# Инициализация и изменение синхронизируются с get_settings(), чтобы источники/
# солверы видели тот же data_source.
_runtime = SettingsOut(
    solver_mode=get_settings().solver_mode,
    dist_mode=get_settings().dist_mode,
    data_source=get_settings().data_source,
    region=get_settings().region,
)


def _settings() -> SettingsOut:
    return _runtime


def _region_box(region: str) -> RegionMeta:
    ds = get_settings().data_source
    if ds == "remote":
        from app import remote_source
        from app.schemas import LatLng

        r = remote_source.load_region(region)
        return RegionMeta(
            id=region,
            name=r["region_name"],
            office_address=r.get("office_address", ""),
            office=LatLng(**r["office"]),
            requests=len(r["requests"]),
            engineers=len(r["engineers"]),
        )
    if ds in ("csv", "db"):
        from app.data_source import load_region
        from app.schemas import LatLng

        r = load_region(region)
        return RegionMeta(
            id=region,
            name=r["region_name"],
            office_address=r["office_address"],
            office=LatLng(**r["office"]),
            requests=len(r["requests"]),
            engineers=len(r["engineers"]),
        )
    meta = next(m for m in mock.list_regions() if m.id == region)
    return meta


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(title=settings.app_name, version="0.1.0")

    @app.get("/api/health")
    def health():
        return {"status": "ok", "app": settings.app_name}

    @app.get("/api/regions", response_model=list[RegionMeta])
    def regions():
        return [_region_box(r) for r in REGIONS]

    @app.get("/api/requests", response_model=list[Request])
    def requests(region: RegionId = Query(default=_settings().region)):
        if get_settings().data_source == "remote":
            from app import remote_source
            return remote_source.requests_for(region)  # все точки: статусы/контроль — справочно
        reqs, _ = planner.dataset(region, active=False)
        return reqs

    @app.get("/api/engineers", response_model=list[Engineer])
    def engineers(region: RegionId = Query(default=_settings().region)):
        _, engs = planner.dataset(region)
        return engs

    @app.get("/api/solvers")
    def solvers():
        from app.solvers import available as solver_available
        from app.solvers import names as solver_names

        return [
            {"name": n, "label": SOLVER_LABELS.get(n, n), "enabled": solver_available(n)}
            for n in solver_names()
        ]

    @app.post("/api/plan", response_model=PlanResponse)
    @app.get("/api/plan", response_model=PlanResponse)
    def plan(region: RegionId = Query(default=_settings().region),
             mode: SolverMode = Query(default=_settings().solver_mode),
             dist: DistMode = Query(default=_settings().dist_mode)):
        from app.solvers import available as solver_available

        if not solver_available(mode):
            raise HTTPException(
                status_code=503,
                detail="Режим benchmark_ortools требует пакет ortools: "
                       "pip install -r requirements-benchmark.txt",
            )
        return planner.solve(region, mode, dist)

    @app.post("/api/scenario", response_model=ScenarioResult)
    def scenario(ev: ScenarioEvent = Body(...),
                 region: RegionId = Query(default=_settings().region)):
        try:
            return planner.replay(region, ev)
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e))

    @app.get("/api/explain", response_model=RequestExplanation)
    def explain(region: RegionId = Query(default=_settings().region),
                request_id: str = Query(...),
                mode: SolverMode = Query(default=_settings().solver_mode),
                dist: DistMode = Query(default=_settings().dist_mode)):
        """Почему заявка назначена именно так (или почему не назначена) — по требованию."""
        from app.solvers import available as solver_available

        if not solver_available(mode):
            raise HTTPException(
                status_code=503,
                detail="Режим benchmark_ortools требует пакет ortools: "
                       "pip install -r requirements-benchmark.txt",
            )
        key = (region, mode, dist)
        plan = session.get(key) or planner.solve(region, mode, dist)
        result = explain_mod.explain(region, request_id, plan)
        if result is None:
            raise HTTPException(status_code=404, detail=f"Заявка {request_id} не найдена в регионе {region}")
        return result

    @app.get("/api/settings", response_model=SettingsOut)
    def get_settings_api():
        return _settings()

    @app.post("/api/settings", response_model=SettingsOut)
    def set_settings_api(body: SettingsIn):
        upd = body.model_dump(exclude_none=True)
        for k, v in upd.items():
            setattr(_runtime, k, v)
            setattr(get_settings(), k, v)
        return _settings()

    # Статика (без кеша, чтобы правки app.js/index.html применялись сразу)
    static_dir = Path(__file__).resolve().parent.parent / "static"
    if static_dir.exists():
        from starlette.staticfiles import StaticFiles

        class _NoCacheStatic(StaticFiles):
            def file_response(self, *a, **kw):
                resp = super().file_response(*a, **kw)
                resp.headers["Cache-Control"] = "no-store, no-cache, must-revalidate"
                return resp

        app.mount("/", _NoCacheStatic(directory=static_dir, html=True), name="static")

    return app