"""Источник данных «API коллеги» (data_source=remote).

GET {REMOTE_BASE_URL}/api/points  — заявки с реальными координатами, статусами,
                                     контрольными бригадами, округом.
GET {REMOTE_BASE_URL}/api/eng     — модель инженеров: навыки (equip), смена
                                     (shift, минуты от 00:00), скорость (speed, км/ч).

Адаптер приводит их формат к нашему контракту (Request/Engineer) и кэширует
в data/parsed/remote_<region>.json, чтобы демо работало и без сети.

Особенности remote-модели:
- статус заявки берём справочно (Request.status); планируем только активные
  (исключая «Отменена»/«Выполнена»);
- у их модели инженеров нет классов транспорта — transport оставляем пустым,
  а скорость берём из speed_kph (поле Engineer), required_transport не накладываем;
- стартовая точка инженеров — наш офис региона (у коллеги офиса нет).
"""

import json
import sys
from pathlib import Path

import requests

from app.config import get_settings
from app.distance import to_hhmm
from app.regions import REGIONS, SKILLS, norm_min, urgency_of
from app.schemas import Engineer, LatLng, Request

PARSED_DIR = Path("data/parsed")

# Статусы BK, исключаемые из планирования (остальные — активные).
ACTIVE_EXCLUDE = {"Отменена", "Выполнена"}


def _region_for_okrug(okrug: str) -> str | None:
    o = (okrug or "").strip().lower()
    if "юго-центр" in o or "югоцентр" in o:
        return "yugocentr"
    if "юго-восток" in o or "юговосток" in o:
        return "yugo_vostok"
    if "восток" in o:
        return "vostok"
    return None


def _get_json(path: str) -> list:
    s = get_settings()
    r = requests.get(s.remote_base_url + path, timeout=s.remote_timeout)
    r.raise_for_status()
    data = r.json()
    if isinstance(data, dict) and "points" in data:
        return data["points"]
    if isinstance(data, list):
        return data
    raise ValueError(f"Unexpected payload from {path}: {type(data)}")


def _window(hhmm_datetime: str) -> tuple[str, str]:
    parts = (hhmm_datetime or "").split()
    if len(parts) >= 2 and ":" in parts[-1]:
        return parts[-1][:5], parts[-1][:5]
    return "", ""


def _parse_date(hhmm_datetime: str) -> str:
    parts = (hhmm_datetime or "").split()
    return parts[0] if parts else ""


def map_request(p: dict) -> Request:
    bk = p.get("request_type_bk") or ""
    skill = "lokalnye"
    for key in ("Подключение", "Дозаказ", "Локальная заявка", "Глобальная проблема"):
        if key in bk:
            from app.regions import BK_TO_SKILL
            skill = BK_TO_SKILL[key]
            break
    ws, we = _window(p.get("start_time") or "")
    return Request(
        id=str(p.get("request_id")),
        bk_type=bk,
        hd_type=p.get("request_type_hd") or "",
        skill=skill,
        skill_label=SKILLS[skill]["label"],
        window_start=ws,
        window_end=we or "23:59",
        district=p.get("district") or "",
        address=p.get("address") or "",
        tech=p.get("Подключение") or None,
        gigabit=bool(p.get("gigabit_connection")),
        priority=urgency_of(bk, p.get("request_type_hd") or ""),
        duration_min=norm_min(bk),
        status=p.get("status_bk") or "",
        control_brigade=brigade_or_none(p.get("brigade")),
        lat=_to_float(p.get("lat")),
        lng=_to_float(p.get("lon")),
    )


def brigade_or_none(v) -> str | None:
    b = (v or "").strip()
    return b if b and b.lower() != "nan" else None


def _to_float(v) -> float | None:
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _equip_to_skills(equip: list) -> tuple[list[str], list[str]]:
    from app.regions import BK_TO_SKILL

    keys: list[str] = []
    for label in equip or []:
        k = BK_TO_SKILL.get(label)
        if k and k not in keys:
            keys.append(k)
    if not keys:
        keys = ["podklyuchenie", "lokalnye", "avariynye"]
    return keys, [SKILLS[k]["label"] for k in keys]


def map_engineers(region: str, raw: list) -> list[Engineer]:
    office = REGIONS[region]["office"]
    out: list[Engineer] = []
    for i, e in enumerate(raw):
        shift = e.get("shift") or [0, 1440]
        start_min, end_min = int(shift[0]), int(shift[1])
        skills, labels = _equip_to_skills(e.get("equip") or [])
        out.append(Engineer(
            id=f"eng-{region}-{i + 1}",
            name=f"Инженер {i + 1}",
            skills=skills,
            skills_label=labels,
            transport="",
            transport_label="по скорости",
            speed_kph=float(e.get("speed") or 0),
            shift_start=to_hhmm(start_min),
            shift_end=to_hhmm(end_min),
            start=office,
        ))
    return out


def build_region(region: str, raw_points: list, raw_eng: list) -> dict:
    reqs = [map_request(p) for p in raw_points if _region_for_okrug(p.get("okrug") or "") == region]
    active = [r for r in reqs if r.status not in ACTIVE_EXCLUDE]
    engs = map_engineers(region, raw_eng) if reqs else []
    date = _parse_date(next((p.get("start_time") for p in raw_points if _region_for_okrug(p.get("okrug") or "") == region), ""))
    parsed = {
        "region": region,
        "region_name": REGIONS[region]["name"],
        "source": "remote",
        "date": date or "17.08.2026",
        "office_address": REGIONS[region]["office_address"],
        "office": REGIONS[region]["office"].model_dump(),
        "requests": [r.model_dump() for r in reqs],
        "active_requests": [r.id for r in active],
        "engineers": [e.model_dump() for e in engs],
    }
    PARSED_DIR.mkdir(parents=True, exist_ok=True)
    (PARSED_DIR / f"remote_{region}.json").write_text(json.dumps(parsed, ensure_ascii=False), encoding="utf-8")
    return parsed


def load_region(region: str) -> dict:
    p = PARSED_DIR / f"remote_{region}.json"
    if p.exists():
        return json.loads(p.read_text(encoding="utf-8"))
    return build_region(region, _get_json("/api/points"), _get_json("/api/eng"))


def requests_for(region: str) -> list[Request]:
    """Все заявки (включая отменённые/выполненные) — для показа на карте/в списках."""
    return [Request(**r) for r in load_region(region)["requests"]]


def active_requests(region: str) -> list[Request]:
    """Заявки, подлежащие планированию (статусы исключены из ACTIVE_EXCLUDE)."""
    parsed = load_region(region)
    allow = set(parsed["active_requests"])
    return [r for r in (Request(**x) for x in parsed["requests"]) if r.id in allow]


def engineers_for(region: str) -> list[Engineer]:
    return [Engineer(**e) for e in load_region(region)["engineers"]]


def office_for(region: str) -> LatLng:
    return LatLng(**load_region(region)["office"])


def plan_date(region: str) -> str:
    return load_region(region).get("date", "")


def control_metrics(region: str) -> dict | None:
    """Контрольное распределение коллеги (brigade) среди активных заявок."""
    active = active_requests(region)
    yes = sum(1 for r in active if r.control_brigade)
    brigades = len({r.control_brigade for r in active if r.control_brigade})
    return {"total": len(active), "assigned_count": yes, "engineers_used": brigades}


def build_all() -> None:
    raw_points = _get_json("/api/points")
    raw_eng = _get_json("/api/eng")
    for region in REGIONS:
        p = build_region(region, raw_points, raw_eng)
        print(json.dumps({
            "region": region,
            "source": "remote",
            "requests": len(p["requests"]),
            "active": len(p["active_requests"]),
            "engineers": len(p["engineers"]),
            "date": p["date"],
        }, ensure_ascii=False))


if __name__ == "__main__":
    if "build" in sys.argv:
        build_all()
    else:
        print("usage: python -m app.remote_source build")