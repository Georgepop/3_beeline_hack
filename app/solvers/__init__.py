"""Реестр солверов. Новый солвер = функция в пакете + строка здесь.
Регистрация через имя, переключение — query-параметр mode в /api/plan."""

from typing import Callable

from app.schemas import Engineer, Request, PlanResponse

SolverFn = Callable[[str, str, list[Request], list[Engineer]], PlanResponse]

from app.solvers.fifo import solve_fifo
from app.solvers.improved import solve_improved

_REGISTRY: dict[str, SolverFn] = {
    "baseline_fifo": solve_fifo,
    "improved": solve_improved,
}


def register(name: str, fn: SolverFn) -> None:
    _REGISTRY[name] = fn


def get(name: str) -> SolverFn:
    return _REGISTRY.get(name, solve_improved)


def names() -> list[str]:
    return list(_REGISTRY)