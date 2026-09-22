"""Утилиты генерации случайных точек (для моков)."""

import random

from app.schemas import LatLng


def _rng(seed: str) -> random.Random:
    return random.Random(seed)


def _point_around(office: LatLng, rng: random.Random, radius_km: float = 3.0):
    # примерно deg на км у Москвы: 1/111 lat, ~1/70 lng
    lat = office.lat + (rng.random() - 0.5) * 2 * radius_km / 111.0
    lng = office.lng + (rng.random() - 0.5) * 2 * radius_km / 70.0
    return LatLng(lat=round(lat, 6), lng=round(lng, 6))