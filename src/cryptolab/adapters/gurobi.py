"""Read documented Gurobi attributes without changing a model or its parameters."""

from importlib import import_module
import math
from typing import Any


# https://docs.gurobi.com/projects/optimizer/en/current/reference/numericcodes/statuscodes.html
STATUS_NAMES = dict(enumerate((
    "LOADED", "OPTIMAL", "INFEASIBLE", "INF_OR_UNBD", "UNBOUNDED", "CUTOFF",
    "ITERATION_LIMIT", "NODE_LIMIT", "TIME_LIMIT", "SOLUTION_LIMIT", "INTERRUPTED",
    "NUMERIC", "SUBOPTIMAL", "INPROGRESS", "USER_OBJ_LIMIT", "WORK_LIMIT", "MEM_LIMIT",
    "LOCALLY_OPTIMAL", "LOCALLY_INFEASIBLE",
), start=1))


def _read(model: Any, name: str) -> Any:
    try:
        return getattr(model, name)
    except Exception:
        # Unavailable attributes raise AttributeError or a solver-specific error.
        return None


def _number(model: Any, name: str) -> int | float | None:
    value = _read(model, name)
    if isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value):
        return value
    return None


def extract(model: Any) -> tuple[dict[str, Any], dict[str, Any]]:
    status = _number(model, "Status")
    sol_count = _number(model, "SolCount")
    has_solution = sol_count is not None and sol_count > 0
    is_mip = _read(model, "IsMIP")
    runtime = _number(model, "Runtime")
    metrics = {
        "Status": status,
        "SolCount": sol_count,
        "Runtime": runtime,
        "ObjVal": _number(model, "ObjVal") if has_solution else None,
        "ObjBound": _number(model, "ObjBound"),
        "MIPGap": _number(model, "MIPGap") if has_solution and is_mip != 0 else None,
        "NodeCount": _number(model, "NodeCount"),
    }
    version = None
    try:
        gurobipy = import_module("gurobipy")
        version = ".".join(str(part) for part in gurobipy.gurobi.version())
    except Exception:
        pass  # Optional integration, including installations missing native libs.
    solver = {
        "solver_name": "Gurobi",
        "solver_version": version,
        "solver_status": STATUS_NAMES.get(status, f"UNKNOWN_{status}") if status is not None else None,
        "solver_runtime_s": runtime,
    }
    return {"gurobi": metrics}, solver
