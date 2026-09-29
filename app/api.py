"""API HTTP-слой (FastAPI). Контракт из app/schemas.py."""

from pathlib import Path

from fastapi import Body, FastAPI, HTTPException, Query

from app import db
from app import explain as explain_mod
from app import planner
from app import repository
from app.config import get_settings
from app.regions import REGIONS
from app.schemas import (
    DbStatus,
    DistMode,
    EditCounts,
    Engineer,
    EngineerPatch,
    EngineerRoute,
    GeometryResponse,
    GeoReverseIn,
    GeoReverseOut,
    PlanResponse,
    RegionId,
    RegionMeta,
    Request,
    RequestDraft,
    RequestExplanation,
    RequestPatch,
    ResetIn,
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
    "benchmark_ortools": "OR-Tools",
    "ortools_office": "Контур (OR-Tools)",
}

# Хранимые настройки (пока in-memory; Phase 5 — таблица settings в БД).
# Инициализация и изменение синхронизируются с get_settings(), чтобы солверы
# видели те же region/mode/dist.
_runtime = SettingsOut(
    solver_mode=get_settings().solver_mode,
    dist_mode=get_settings().dist_mode,
    region=get_settings().region,
)


def _settings() -> SettingsOut:
    return _runtime


def _region_box(region: str) -> RegionMeta:
    meta = repository.region_meta(region)
    if meta is None:
        raise HTTPException(status_code=404, detail=f"Регион {region} не найден в базе")
    return meta


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(title=settings.app_name, version="0.1.0")
    # БД готова к работе всегда: на пустой импортируем из CSV, иначе приложение
    # поднялось бы с пустыми регионами и тихо считало бы нечего.
    db.ensure_initialized()

    @app.get("/api/health")
    def health():
        return {"status": "ok", "app": settings.app_name}

    @app.get("/api/regions", response_model=list[RegionMeta])
    def regions():
        return [_region_box(r) for r in REGIONS]

    @app.get("/api/requests", response_model=list[Request])
    def requests(region: RegionId = Query(default=_settings().region)):
        # active=False: карте и спискам нужны и те заявки, что не планируются.
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

    # --- Правка данных (БД) ---
    #
    # После любой правки кеш плана промахивается сам: отпечаток в app/session.py
    # считается по данным, а данные берутся из repository. Поэтому ручных
    # invalidate здесь нет — иначе легко забыть один из путей и показать
    # пользователю план, посчитанный до правки.

    @app.post("/api/requests", response_model=Request)
    def create_request(draft: RequestDraft,
                       region: RegionId = Query(default=_settings().region)):
        _region_box(region)
        address = (draft.address or "").strip()
        if not address:
            raise HTTPException(status_code=400, detail="Укажите адрес заявки")
        payload = draft.model_dump()
        if payload.get("lat") is None or payload.get("lng") is None:
            # Адрес без координат солвер планировать не сможет, поэтому геокодируем
            # сами. Не нашли — не отказываем: заявка останется на карте кликом.
            from app import geo

            coord = geo.geocode(address, draft.district or "")
            if coord:
                payload["lat"], payload["lng"] = coord[0], coord[1]
        try:
            return repository.create_request(region, payload)
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e))

    @app.patch("/api/requests/{request_id}", response_model=Request)
    def patch_request(request_id: str, patch: RequestPatch,
                      region: RegionId = Query(default=_settings().region)):
        _region_box(region)
        changed = patch.model_dump(exclude_unset=True, exclude_none=True)
        try:
            out = repository.update_request(region, request_id, changed)
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e))
        if out is None:
            raise HTTPException(
                status_code=404, detail=f"Заявка {request_id} не найдена в регионе {region}"
            )
        return out

    @app.delete("/api/requests/{request_id}")
    def drop_request(request_id: str,
                     region: RegionId = Query(default=_settings().region)):
        _region_box(region)
        if not repository.delete_request(region, request_id):
            raise HTTPException(
                status_code=404, detail=f"Заявка {request_id} не найдена в регионе {region}"
            )
        return {"deleted": request_id}

    @app.patch("/api/engineers/{engineer_id}", response_model=Engineer)
    def patch_engineer(engineer_id: str, patch: EngineerPatch,
                       region: RegionId = Query(default=_settings().region)):
        _region_box(region)
        changed = patch.model_dump(exclude_unset=True, exclude_none=True)
        for k in ("skills", "transport"):
            if k in changed and changed[k] not in (None, "", []):
                from app.regions import SKILLS, TRANSPORT

                table = SKILLS if k == "skills" else TRANSPORT
                values = changed[k] if k == "skills" else [changed[k]]
                for v in values:
                    if v not in table:
                        raise HTTPException(
                            status_code=400,
                            detail=f"Неизвестное значение {k}={v}",
                        )
        if "shift_start" in changed or "shift_end" in changed:
            from app.distance import parse_hhmm, to_hhmm

            cur = repository.get_engineer(region, engineer_id)
            if cur is None:
                raise HTTPException(
                    status_code=404,
                    detail=f"Инженер {engineer_id} не найден в регионе {region}",
                )
            start = changed.get("shift_start", cur.shift_start)
            end = changed.get("shift_end", cur.shift_end)
            if parse_hhmm(end) <= parse_hhmm(start):
                raise HTTPException(
                    status_code=400, detail=f"Смена {start}-{end} пустая или вывернута"
                )
            changed.setdefault("shift_start", to_hhmm(parse_hhmm(start)))
            changed.setdefault("shift_end", to_hhmm(parse_hhmm(end)))
        out = repository.update_engineer(region, engineer_id, changed)
        if out is None:
            raise HTTPException(
                status_code=404,
                detail=f"Инженер {engineer_id} не найден в регионе {region}",
            )
        return out

    @app.get("/api/db/status", response_model=DbStatus)
    def db_status(region: RegionId = Query(default=_settings().region)):
        meta = _region_box(region)
        st = repository.edit_status(region)
        req, eng = st["requests"], st["engineers"]
        return DbStatus(
            region=region,
            region_name=meta.name,
            requests=EditCounts(**req),
            engineers=EditCounts(**eng),
            dirty=any(
                v for v in (req["added"], req["edited"], req["deleted"],
                            eng["added"], eng["edited"], eng["deleted"])
            ),
        )

    @app.post("/api/db/reset")
    def db_reset(body: ResetIn,
                 region: RegionId = Query(default=_settings().region)):
        _region_box(region)
        if not (body.requests or body.engineers):
            raise HTTPException(
                status_code=400, detail="Отметьте хотя бы заявки или инженеров"
            )
        with db.session_scope() as s:
            repository.reset_region(s, region, body.requests, body.engineers)
        st = repository.edit_status(region)
        return {
            "reset": {"requests": body.requests, "engineers": body.engineers},
            "requests": st["requests"],
            "engineers": st["engineers"],
        }

    @app.post("/api/geo/reverse", response_model=GeoReverseOut)
    def geo_reverse(body: GeoReverseIn):
        from app import geo

        return GeoReverseOut(**geo.reverse(body.lat, body.lng))

    @app.post("/api/plan", response_model=PlanResponse)
    @app.get("/api/plan", response_model=PlanResponse)
    def plan(region: RegionId = Query(default=_settings().region),
             mode: SolverMode = Query(default=_settings().solver_mode),
             dist: DistMode = Query(default=_settings().dist_mode)):
        from app.solvers import available as solver_available

        if not solver_available(mode):
            raise HTTPException(
                status_code=503,
                detail=f"Режим {mode} требует пакет ortools: "
                       "pip install -r requirements-benchmark.txt",
            )
        return planner.solve(region, mode, dist)

    @app.get("/api/plan/geometry", response_model=GeometryResponse)
    def plan_geometry(region: RegionId = Query(default=_settings().region),
                      mode: SolverMode = Query(default=_settings().solver_mode),
                      dist: DistMode = Query(default=_settings().dist_mode)):
        """Полилинии маршрутов по дорогам — для карты, не для расчёта.

        Расписание считается на haversine и сюда не входит, поэтому запрос идёт
        отдельным вызовом уже после /api/plan: сетевая задержка OSRM не стоит в
        критическом пути. Недоступный OSRM — не ошибка: отдаём route=None, и фронт
        рисует прямую линию (так и было до разделения).
        """
        from app import osrm

        plan = planner.solve(region, mode, dist)  # из кэша плана — мгновенно
        active = [e for e in plan.engineers if e.stops]
        paths = [[(e.start.lat, e.start.lng)] + [(s.lat, s.lng) for s in e.stops]
                 for e in active]
        try:
            polylines = osrm.routes_polyline(paths)
        except Exception:
            polylines = [None] * len(paths)
        return GeometryResponse(
            region=region,
            mode=mode,
            routes=[EngineerRoute(engineer_id=e.id, route=r)
                    for e, r in zip(active, polylines)],
        )

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
                detail=f"Режим {mode} требует пакет ortools: "
                       "pip install -r requirements-benchmark.txt",
            )
        plan = planner.solve(region, mode, dist)
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
        # Смена региона меняет набор заявок, но отдельного сброса кеша не нужно:
        # регион входит в ключ, а у другого набора заявок будет другой отпечаток —
        # промах случится сам собой.
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