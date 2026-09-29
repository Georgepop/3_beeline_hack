"""Замер планирования: 3 региона × 3 режима, потолок смены, причины отказов.

Инструмент только читает данные и печатает отчёт — файлы проекта не меняет.
Переопределения настроек, нормативов, транспорта и числа исполнителей делаются
в памяти на время прогона, поэтому результат сопоставим с пересборкой данных,
но не требует её.

CLI:
    python -m app.bench
    python -m app.bench --region yugo_vostok --modes improved benchmark_ortools
    python -m app.bench --time-limit 30 --fixed-cost 20
    python -m app.bench --norm "Дозаказ=50" --transport-mix auto=6,transit=3,bike=3
    python -m app.bench --engineers +3 --repeats 3
"""

import argparse
import statistics
import sys

from app import distance as ds
from app.config import get_settings
from app.regions import REGIONS, SKILLS, norm_min
from app.repository import control_metrics, engineers_for, requests_for
from app.schemas import Engineer, PlanResponse, Request
from app.solvers import get as get_solver

ALL_SKILLS = list(SKILLS.keys())


# --- переопределения в памяти ------------------------------------------------

def _apply_settings(args) -> dict:
    """Записывает орагловые переопределения в кэшированные настройки."""
    s = get_settings()
    applied: dict[str, object] = {}
    # osrm_enabled НЕ выключаем. Раньше здесь стояло s.osrm_enabled = False «чтобы
    # замер не дёргал OSRM за полилиниями», но солверы полилинии давно не берут
    # (роут из with_route вынесен в GET /api/plan/geometry), а «Контур» берёт у
    # OSRM матрицу расстояний и отключение флага молча уводило его на haversine —
    # в таблице замера это выглядело бы как результат, посчитанный другой моделью.
    # Чем считали — печатается в строке режима.
    if args.office_matrix is not None:
        s.ortools_office_matrix = args.office_matrix
        applied["ortools_office_matrix"] = args.office_matrix
    if args.time_limit is not None:
        s.ortools_time_limit = args.time_limit
        applied["ortools_time_limit"] = args.time_limit
    if args.fixed_cost is not None:
        s.ortools_fixed_vehicle_cost = args.fixed_cost
        applied["ortools_fixed_vehicle_cost"] = args.fixed_cost
    if args.slack is not None:
        s.ortools_slack_max = args.slack
        applied["ortools_slack_max"] = args.slack
    if args.first_solution:
        s.ortools_first_solution = args.first_solution
        applied["ortools_first_solution"] = args.first_solution
    if args.local_search:
        s.ortools_local_search = args.local_search
        applied["ortools_local_search"] = args.local_search
    return applied


def _apply_norms(reqs: list[Request], specs: list[str]) -> None:
    """Пересчитывает duration_min по bk_type (эквивалент пересборки данных)."""
    for spec in specs:
        bk_type, _, raw = spec.partition("=")
        bk_type, minutes = bk_type.strip(), int(raw)
        for r in reqs:
            if r.bk_type == bk_type:
                r.duration_min = minutes


def _transport_mix(spec: str) -> list[str]:
    """'auto=6,transit=3' -> ['auto']*6 + ['transit']*3."""
    out: list[str] = []
    for part in spec.split(","):
        name, _, count = part.partition("=")
        out += [name.strip()] * int(count or 1)
    return out


def _apply_transport(engs: list[Engineer], spec: str) -> int:
    mix = _transport_mix(spec)
    if not mix:
        return 0
    changed = 0
    for i, e in enumerate(engs):
        want = mix[i % len(mix)]
        if e.transport != want:
            changed += 1
        e.transport = want
    return changed


def _add_engineers(engs: list[Engineer], region: str, extra: int) -> None:
    """Синтетические исполнители: все навыки + автомобиль (максимальная гибкость)."""
    base = engs[0] if engs else None
    for k in range(extra):
        engs.append(Engineer(
            id=f"eng-{region}-synth-{k + 1}",
            name=f"Резерв {k + 1}",
            skills=list(ALL_SKILLS),
            skills_label=[SKILLS[s]["label"] for s in ALL_SKILLS],
            transport="auto",
            transport_label="Автомобиль",
            shift_start=base.shift_start if base else "08:00",
            shift_end=base.shift_end if base else "20:00",
            start=base.start if base else None,
        ))


# --- метрики -----------------------------------------------------------------

def _ceiling(reqs: list[Request], engs: list[Engineer]) -> tuple[int, int, int]:
    """(всего заявок, потолок смены, структурно невозможных).

    Потолок — заявки, которые в принципе назначаемы: есть координаты, есть
    подходящий инженер, окно помещается в смену целиком.
    """
    shift_a = ds.parse_hhmm(engs[0].shift_start) if engs else 0
    shift_b = ds.parse_hhmm(engs[0].shift_end) if engs else 0
    eligible = lambda r: any(
        r.skill in e.skills and (not r.required_transport or e.transport == r.required_transport)
        for e in engs
    )
    ok = impossible = 0
    for r in reqs:
        if r.lat is None or r.lng is None or not eligible(r):
            continue
        ws, we = ds.parse_hhmm(r.window_start), ds.parse_hhmm(r.window_end)
        fits = ws + r.duration_min <= shift_b and we - r.duration_min >= shift_a
        if fits:
            ok += 1
        else:
            impossible += 1
    return len(reqs), ok, impossible


def _run(region: str, mode: str, reqs: list[Request], engs: list[Engineer]) -> PlanResponse:
    return get_solver(mode)(region, "haversine", reqs, engs)


def _median_runs(region: str, mode: str, reqs: list[Request], engs: list[Engineer],
                 repeats: int) -> dict:
    """Медиана по нескольким прогонам: в ortools 9.15 нет seed, результат гуляет."""
    plans = [_run(region, mode, reqs, engs) for _ in range(max(1, repeats))]
    pick = lambda attr: statistics.median([getattr(p.metrics, attr) for p in plans])
    return {
        "plans": plans,
        "assigned": [p.metrics.assigned_count for p in plans],
        "assigned_med": pick("assigned_count"),
        "engineers": pick("engineers_used"),
        "km": pick("total_km"),
        "minutes": pick("total_minutes"),
        "unassigned": pick("unassigned_count"),
        "matrix": plans[0].matrix,
        "spread": (min(p.metrics.assigned_count for p in plans),
                   max(p.metrics.assigned_count for p in plans))
        if len(plans) > 1 else None,
    }


def _fmt_row(cells: list[str], widths: list[int]) -> str:
    return "  ".join(c.ljust(w) for c, w in zip(cells, widths))


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="app.bench", description="Замер планирования по регионам и режимам")
    p.add_argument("--region", action="append", choices=list(REGIONS), help="по умолчанию все")
    p.add_argument("--mode", action="append", dest="modes",
                   choices=["baseline_fifo", "improved", "benchmark_ortools", "ortools_office"],
                   help="по умолчанию все")
    p.add_argument("--repeats", type=int, default=3, help="прогонов на режим (медиана; для детерминированных 1)")
    p.add_argument("--time-limit", type=int, help="ortools_time_limit, сек")
    p.add_argument("--fixed-cost", type=int, help="ortools_fixed_vehicle_cost, мин")
    p.add_argument("--slack", type=int, help="ortools_slack_max, мин")
    p.add_argument("--office-matrix", choices=["osrm", "haversine"],
                   help="чем считать длины дуг в режиме «Контур» (по умолчанию из настроек)")
    p.add_argument("--first-solution", help="ortools_first_solution")
    p.add_argument("--local-search", help="ortools_local_search")
    p.add_argument("--norm", action="append", default=[], help='норматив: "Дозаказ=50"')
    p.add_argument("--transport-mix", help='"auto=6,transit=3,bike=3"')
    p.add_argument("--engineers", type=int, default=0, help="добавить синтетических исполнителей (+N)")
    p.add_argument("--compact", action="store_true", help="только сводная таблица")
    args = p.parse_args(argv)

    applied = _apply_settings(args)
    regions = args.region or list(REGIONS)
    modes = args.modes or ["baseline_fifo", "improved", "benchmark_ortools", "ortools_office"]

    print("настройки замера:", applied or "по умолчанию")
    if args.norm:
        print("нормативы (в памяти):", args.norm)
    if args.transport_mix:
        print("транспорт (в памяти):", args.transport_mix)
    if args.engineers:
        print(f"исполнителей добавлено: +{args.engineers} (все навыки, автомобиль)")
    print()

    widths = [11, 17, 9, 10, 11, 10, 10, 7]
    print(_fmt_row(["регион", "режим", "назначено", "неназнач.", "использ.", "пробег,км", "время,мин", "разброс"], widths))
    print("-" * (sum(widths) + 2 * len(widths)))

    summary: list[dict] = []
    for region in regions:
        reqs = requests_for(region)
        engs = engineers_for(region)
        _apply_norms(reqs, args.norm)
        if args.transport_mix:
            _apply_transport(engs, args.transport_mix)
        if args.engineers:
            _add_engineers(engs, region, args.engineers)

        total, ceil, impossible = _ceiling(reqs, engs)
        control = control_metrics(region) or {}
        if not args.compact:
            print()
            print(f"# {region} — заявок {total}, потолок смены {ceil}, "
                  f"структурно невозможных {impossible}, исполнителей {len(engs)}, "
                  f"контрольное распределение {control.get('assigned_count', '?')}/{total} "
                  f"на {control.get('engineers_used', '?')} бригадах")

        for mode in modes:
            repeats = args.repeats if mode in ("benchmark_ortools", "ortools_office") else 1
            r = _median_runs(region, mode, reqs, engs, repeats)
            spread = f"{r['spread'][0]}-{r['spread'][1]}" if r["spread"] else ""
            print(_fmt_row([region, mode, str(int(r["assigned_med"])), str(int(r["unassigned"])),
                            str(int(r["engineers"])), f"{r['km']:.1f}", str(int(r["minutes"])), spread], widths))
            # Чем посчитан план: у «Контура» дорожная матрица OSRM, и при недоступной
            # сети он молча уходит на haversine. Без этой пометки строки замера
            # нельзя сравнивать между прогонами — цифры относятся к разной модели.
            if r["matrix"] and not args.compact:
                note = ""
                if r["matrix"] != get_settings().ortools_office_matrix:
                    note = "  ← OSRM не ответил, откат на прямые (замер нельзя сравнивать с osrm)"
                print(f"    матрица расстояний: {r['matrix']}{note}")
            summary.append({"region": region, "mode": mode, **{k: r[k] for k in
                             ("assigned_med", "unassigned", "engineers", "km", "minutes", "assigned", "matrix")},
                             "ceiling": ceil, "impossible": impossible, "control": control})

            if args.compact:
                continue
            for eng in r["plans"][0].engineers:
                print(f"    {eng.name[:22]:<22} {len(eng.stops):>2} заявок  {eng.km:>6.1f} км  {eng.minutes:>4} мин  [{eng.transport}]")
            hist: dict[str, int] = {}
            for u in r["plans"][0].unassigned:
                hist[u.reason] = hist.get(u.reason, 0) + 1
            if hist:
                print("    причины невыполнения:")
                for reason, cnt in sorted(hist.items(), key=lambda x: -x[1]):
                    print(f"      {cnt:>3}  {reason}")

    print()
    print("сводка (назначено / потолок):")
    for region in regions:
        cells = [region]
        for mode in modes:
            row = next((x for x in summary if x["region"] == region and x["mode"] == mode), None)
            cells.append(f"{int(row['assigned_med'])}/{row['ceiling']}" if row else "-")
        print("  " + _fmt_row(cells, [11] + [20] * len(modes)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
