"""Мок-данные (используются при DATA_SOURCE=mock) — детерминированно.

Реальные данные — app.data_source (CSV из data/raw). Мок остаётся для
быстрой работы фронта без подготовленных данных.
"""

import random

from app.config import get_settings
from app.distance import to_hhmm
from app.regions import REGIONS, SKILLS, TRANSPORT, norm_min, urgency_of
from app.schemas import Engineer, LatLng, RegionMeta, Request
from app.random_gen_utils import _point_around, _rng

PER_REGION = {"vostok": 20, "yugo_vostok": 16, "yugocentr": 14}

BRIGADES = {
    "vostok": ["Соколов", "Мельников", "Матвеев", "Попов", "Арташкин", "Комарь", "Каушнян"],
    "yugo_vostok": ["Паршин", "Андреев", "Белоновский", "Рыбин", "Горбанев", "Бузань"],
    "yugocentr": ["Капитанчук", "Исхаков", "Брюзгин", "Бахарев", "Прокопенко", "Выговский"],
}

DISTRICTS = {
    "vostok": ["Кузьминки", "Таганский", "Текстильщики", "Рязанский", "Нижегородский", "Лефортово", "Выхино", "Басманный"],
    "yugo_vostok": ["Домодедово", "Орехово Борисово Южное", "Зябликово", "Бирюлево Восточное", "Кашира", "Братеево", "Царицыно"],
    "yugocentr": ["Даниловский", "Академический", "Котловка", "Зюзино", "Хамовники", "Нагатино - Садовники", "Нагорный"],
}

_BKTYPES = [
    ("Подключение", "podklyuchenie", 5),
    ("Дозаказ", "podklyuchenie", 1),
    ("Локальная заявка", "lokalnye", 4),
    ("Глобальная проблема", "avariynye", 1),
]
_STREETS = ["Ленинский пр-т", "Волгоградский пр-т", "ул. Профсоюзная", "Каширское ш.", "ул. Люблинская", "пр-т Андропова", "ул. Белозерская", "Рязанский пр-т", "ул. Мячковский б-р", "ул. Перерва"]


def build_requests(region: str) -> list[Request]:
    cfg = REGIONS[region]
    rng = _rng(region + ":req")
    n = PER_REGION[region]
    districts = DISTRICTS[region]
    office = cfg["office"]
    out: list[Request] = []
    for i in range(1, n + 1):
        bk, skill, _ = rng.choices(_BKTYPES, weights=[x[2] for x in _BKTYPES])[0]
        urgent = urgency_of(bk) == "urgent"
        hd = "Авария" if urgent else ""
        if urgent:
            wstart, wend = "08:00", "23:59"
        else:
            wstart = to_hhmm(rng.randint(8 * 60, 17 * 60))
            from app.distance import parse_hhmm
            wend = to_hhmm(parse_hhmm(wstart) + rng.randint(90, 240))
        gigabit = rng.random() < 0.05
        pt = _point_around(office, rng)
        out.append(Request(
            id=f"{region[:3].upper()}-{i:04d}",
            bk_type=bk,
            hd_type=hd,
            skill=skill,
            skill_label=SKILLS[skill]["label"],
            window_start=wstart,
            window_end=wend,
            district=rng.choice(districts),
            address=f"г. Москва, {rng.choice(_STREETS)}, д. {rng.randint(1, 120)}к{rng.randint(1, 6)}",
            gigabit=gigabit,
            priority="urgent" if urgent else "normal",
            duration_min=norm_min(bk),
            required_transport="auto" if (gigabit and rng.random() < 0.5) else None,
            lat=round(pt.lat, 6),
            lng=round(pt.lng, 6),
        ))
    return out


def build_engineers(region: str) -> list[Engineer]:
    cfg = REGIONS[region]
    rng = _rng(region + ":eng")
    names = BRIGADES[region]
    office = cfg["office"]
    shifts = get_settings()
    engs: list[Engineer] = []
    for i, fam in enumerate(names):
        base = list(SKILLS.keys())
        primary = base[i % len(base)]
        extra = [b for b in base if b != primary and rng.random() < 0.6]
        transport = rng.choices(list(TRANSPORT.keys()), weights=[1, 4, 1, 4])[0]
        if "auto" not in [e.transport for e in engs]:
            transport = "auto"
        engs.append(Engineer(
            id=f"eng-{region}-{i + 1}",
            name=f"{fam}",
            skills=[primary] + extra,
            skills_label=[SKILLS[s]["label"] for s in [primary] + extra],
            transport=transport,
            transport_label=TRANSPORT[transport]["label"],
            shift_start=shifts.shift_start,
            shift_end=shifts.shift_end,
            start=office,
        ))
    return engs


def list_regions() -> list[RegionMeta]:
    return [RegionMeta(
        id=rid,
        name=cfg["name"],
        office_address=cfg["office_address"],
        office=cfg["office"],
        requests=PER_REGION[rid],
        engineers=len(BRIGADES[rid]),
    ) for rid, cfg in REGIONS.items()]