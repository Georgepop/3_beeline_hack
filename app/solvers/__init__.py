"""Реестр солверов. Новый солвер = функция в пакете + строка здесь.
Регистрация через имя, переключение — query-параметр mode в /api/plan."""

from typing import Callable

from app.schemas import Engineer, Request, PlanResponse

SolverFn = Callable[[str, str, list[Request], list[Engineer]], PlanResponse]

from app.solvers.fifo import solve_fifo
from app.solvers.improved import solve_improved
from app.solvers.ortools_solver import is_available as ortools_available
from app.solvers.ortools_solver import solve_ortools

_REGISTRY: dict[str, SolverFn] = {
    "baseline_fifo": solve_fifo,
    "improved": solve_improved,
    "benchmark_ortools": solve_ortools,
}


def register(name: str, fn: SolverFn) -> None:
    _REGISTRY[name] = fn


def get(name: str) -> SolverFn:
    return _REGISTRY.get(name, solve_improved)


def available(name: str) -> bool:
    """Режим считается доступным, если его реально можно посчитать (ortools — опционален)."""
    if name == "benchmark_ortools":
        return ortools_available()
    return True


def names() -> list[str]:
    return list(_REGISTRY)