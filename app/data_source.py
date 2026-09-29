"""Источник данных: сырые CSV (копии в data/raw/) → действительные заявки и инженеры.

- Синтетический файл: заявки (типы → навык, окна, нормативы, приоритет аварий) +
  офис региона (строка «Адрес Офиса» в конце).
- Контрольный файл: набор бригад → инженеры (навыки по типам выполненных ими работ;
  транспорт — синтетический, как позволили эксперты).
- Геокодирование с кэшем (Nominatim → fallback по району).
- Результат кэшируется в data/parsed/<region>.json, чтобы сервер стартовал мгновенно.

CLI:  python -m app.data_source build  — пересобрать все регионы.
"""

import csv
import io
import json
import sys
from pathlib import Path

from app.config import get_settings
from app.geo import clean_display_address, geocode
from app.regions import (
    REGIONS,
    SKILLS,
    TRANSPORT,
    norm_min,
    skill_of,
    urgency_of,
)
from app.schemas import Engineer, LatLng, Request

RAW_DIR = Path("data/raw")
PARSED_DIR = Path("data/parsed")


def _read_csv(path: Path) -> list[list[str]]:
    data = path.read_bytes().decode("cp1251")
    return list(csv.reader(io.StringIO(data), delimiter=";"))


def parse_office(rows: list[list[str]]) -> str:
    """Офис — строка в конце файла с маркером «Адрес Офиса»/«Адрес офиса»."""
    for r in reversed(rows):
        if not r:
            continue
        if r[0] and ("офис" in r[0].strip().lower() or "офиса" in r[0].strip().lower()):
            return (r[1] or "").strip()
    return ""


def _time_hhmm(value: str) -> str:
    value = value.strip()
    for sep in (" ", "T"):
        if sep in value:
            value = value.split(sep)[1]
    parts = value.split(":")
    return f"{int(parts[0]):02d}:{int(parts[1]):02d}"


def parse_requests(region: str, office_address: str) -> list[Request]:
    rdir = RAW_DIR / region
    syn = _find_synthetic(rdir)
    rows = _read_csv(syn)
    header = rows[0]
    idx = {name: i for i, name in enumerate(header)}
    del rows[0]
    out: list[Request] = []
    for r in rows:
        if not r or not r[0].strip():
            continue
        if r[0].strip() and ("офис" in r[0].strip().lower()):
            continue
        if len(r) < len(header):
            r = r + [""] * (len(header) - len(r))
        req_id = r[idx["Заявка"]].strip()
        bk = r[idx["Тип заявки BK"]].strip()
        hd = r[idx["Тип заявки HD"]].strip()
        addr = r[idx["Адрес"]].strip()
        district = r[idx["Район"]].strip()
        gigabit_col = idx.get("Гигабитное подключение")
        gigabit = bool(gigabit_col is not None and r[gigabit_col].strip().lower() == "да")
        pt = geocode(addr, district)
        out.append(Request(
            id=req_id,
            bk_type=bk,
            hd_type=hd,
            skill=skill_of(bk, hd),
            skill_label=SKILLS[skill_of(bk, hd)]["label"],
            window_start=_time_hhmm(r[idx["Начало"]]),
            window_end=_time_hhmm(r[idx["Окончание"]]),
            district=district,
            address=clean_display_address(addr),
            gigabit=gigabit,
            priority="urgent" if urgency_of(bk, hd) == "urgent" else "normal",
            duration_min=norm_min(bk),
            required_transport="auto" if gigabit else None,
            lat=round(pt[0], 6) if pt else None,
            lng=round(pt[1], 6) if pt else None,
        ))
    return out


def control_metrics(region: str) -> dict | None:
    """Справочные метрики контрольного распределения (что делал реальный диспетчер)."""
    rdir = RAW_DIR / region
    ctrl = _find_control(rdir)
    if not ctrl:
        return None
    rows = _read_csv(ctrl)
    header = rows[0]
    if "Бригада" not in header:
        return None
    bi = header.index("Бригада")
    brigades = set()
    count = 0
    total = 0
    for r in rows[1:]:
        if not r:
            continue
        total += 1
        if r[bi].strip():
            brigades.add(r[bi].strip())
            count += 1
    return {"total": total, "assigned_count": count, "engineers_used": len(brigades)}


def _find_synthetic(rdir: Path) -> Path:
    for p in rdir.glob("*.csv"):
        if "Синтети" in p.name:
            return p
    raise FileNotFoundError(f"Синтетический файл не найден в {rdir}")


def _find_control(rdir: Path) -> Path | None:
    for p in rdir.glob("*.csv"):
        if "Контроль" in p.name:
            return p
    return None


def parse_engineers(region: str) -> list[Engineer]:
    rdir = RAW_DIR / region
    office = REGIONS[region]["office"]
    ctrl = _find_control(rdir)
    groups: dict[str, set[str]] = {}
    if ctrl:
        rows = _read_csv(ctrl)
        header = rows[0]
        idx = {name: i for i, name in enumerate(header)}
        del rows[0]
        for r in rows:
            if not r or not r[0].strip():
                continue
            brigade = r[idx["Бригада"]].strip() if "Бригада" in idx else ""
            bk = r[idx["Тип заявки BK"]].strip() if "Тип заявки BK" in idx else ""
            hd = r[idx["Тип заявки HD"]].strip() if "Тип заявки HD" in idx else ""
            if brigade:
                groups.setdefault(brigade, set()).add(skill_of(bk, hd))
    if not groups:
        # Раньше здесь был откат на синтетические бригады app/mock.py. Модуль
        # удалён вместе с переключателем источника: контрольный файл есть в
        # каждом регионе, поэтому ветка не срабатывала никогда.
        raise FileNotFoundError(f"Контрольный файл не найден в {rdir}")

    s = get_settings()
    engs: list[Engineer] = []
    ordered = sorted(groups.items())
    for i, (raw_name, skills) in enumerate(ordered):
        name = raw_name.replace("Бригада ", "").strip() or f"Инженер {i + 1}"
        skills = sorted(s for s in skills if s in SKILLS)
        transport_pool = ["walk", "transit", "transit", "bike"]
        # минимум 1–2 инженера на автомобиле в регионе
        if i < 2:
            transport = "auto"
        else:
            transport = transport_pool[i % len(transport_pool)]
        engs.append(Engineer(
            id=f"eng-{region}-{i + 1}",
            name=name,
            skills=skills,
            skills_label=[SKILLS[s]["label"] for s in skills],
            transport=transport,
            transport_label=TRANSPORT[transport]["label"],
            shift_start=s.shift_start,
            shift_end=s.shift_end,
            start=office,
        ))
    return engs


def build_region(region: str) -> dict:
    """Действительная модель региона: requests + engineers + office."""
    rdir = RAW_DIR / region
    syn = _find_synthetic(rdir)
    office_address = parse_office(_read_csv(syn)) or REGIONS[region]["office_address"]
    office_pt = geocode(office_address, "")
    requests = parse_requests(region, office_address)
    engineers = parse_engineers(region)
    office = LatLng(
        lat=office_pt[0] if office_pt else REGIONS[region]["office"].lat,
        lng=office_pt[1] if office_pt else REGIONS[region]["office"].lng,
        name=f"Офис {REGIONS[region]['name']}",
    )
    parsed = {
        "region": region,
        "region_name": REGIONS[region]["name"],
        "office_address": office_address,
        "office": office.model_dump(),
        "requests": [r.model_dump() for r in requests],
        "engineers": [e.model_dump() for e in engineers],
    }
    PARSED_DIR.mkdir(parents=True, exist_ok=True)
    (PARSED_DIR / f"{region}.json").write_text(json.dumps(parsed, ensure_ascii=False), encoding="utf-8")
    return parsed


def load_region(region: str) -> dict:
    p = PARSED_DIR / f"{region}.json"
    if p.exists():
        return json.loads(p.read_text(encoding="utf-8"))
    return build_region(region)


def requests_for(region: str) -> list[Request]:
    return [Request(**r) for r in load_region(region)["requests"]]


def engineers_for(region: str) -> list[Engineer]:
    return [Engineer(**e) for e in load_region(region)["engineers"]]


def office_for(region: str) -> LatLng:
    return LatLng(**load_region(region)["office"])


def build_all() -> None:
    for region in REGIONS:
        parsed = build_region(region)
        stats = {
            "region": region,
            "requests": len(parsed["requests"]),
            "engineers": len(parsed["engineers"]),
            "office": parsed["office_address"],
            "uncoords": sum(1 for r in parsed["requests"] if r["lat"] is None),
        }
        print(json.dumps(stats, ensure_ascii=False))


if __name__ == "__main__":
    if "build" in sys.argv:
        build_all()
    else:
        print("usage: python -m app.data_source build")