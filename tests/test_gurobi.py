import json
from types import SimpleNamespace

import pytest

from cryptolab import track_run
from cryptolab.adapters.gurobi import extract


class FakeModel:
    def __init__(self, **attributes):
        self.attributes = attributes
        self.reads = []

    def __getattr__(self, name):
        self.reads.append(name)
        if name not in self.attributes:
            raise RuntimeError(f"Attribute {name} unavailable")
        return self.attributes[name]


@pytest.fixture(autouse=True)
def no_solver_dependency(monkeypatch):
    def missing(name):
        raise ImportError("no Gurobi in this test environment")
    monkeypatch.setattr("cryptolab.adapters.gurobi.import_module", missing)


@pytest.mark.parametrize("status,name,solutions", [
    (2, "OPTIMAL", 1), (9, "TIME_LIMIT", 1), (9, "TIME_LIMIT", 0),
    (3, "INFEASIBLE", 0), (11, "INTERRUPTED", 1), (123, "UNKNOWN_123", 0),
])
def test_solver_status_independent_of_run_status(status, name, solutions, query):
    model = FakeModel(Status=status, SolCount=solutions, Runtime=3.25, ObjVal=16.0,
                      ObjBound=15.0, MIPGap=0.0625, NodeCount=10.0, IsMIP=1)
    with track_run(project="p", experiment="e") as run:
        run.log_metrics({"extra": 42})
        run.log_gurobi(model)
    row = query("SELECT * FROM runs")[0]
    metrics = json.loads(row["metrics_json"])
    assert row["status"] == "COMPLETED"
    assert row["solver_status"] == name
    assert row["solver_runtime_s"] == 3.25
    assert row["solver_name"] == "Gurobi"
    assert row["solver_version"] is None
    assert metrics["extra"] == 42
    assert metrics["gurobi"]["ObjVal"] == (16.0 if solutions else None)
    if not solutions:
        assert "ObjVal" not in model.reads and "MIPGap" not in model.reads


def test_non_mip_does_not_read_gap_and_unavailable_attributes_are_null():
    model = FakeModel(Status=2, SolCount=1, IsMIP=0, ObjVal=2.0)
    metrics, solver = extract(model)
    assert "MIPGap" not in model.reads
    for key in ("MIPGap", "Runtime", "ObjBound", "NodeCount"):
        assert metrics["gurobi"][key] is None
    assert solver["solver_runtime_s"] is None


def test_missing_solution_count_does_not_read_objective():
    model = FakeModel(Status=9, ObjVal=0)
    metrics, _ = extract(model)
    assert metrics["gurobi"]["ObjVal"] is None
    assert "ObjVal" not in model.reads


def test_nonfinite_attributes_are_null():
    model = FakeModel(Status=9, SolCount=1, IsMIP=1, Runtime=float("nan"),
                      ObjVal=float("inf"), MIPGap=float("inf"), ObjBound=float("-inf"))
    metrics, solver = extract(model)
    assert all(metrics["gurobi"][key] is None for key in ("Runtime", "ObjVal", "MIPGap", "ObjBound"))
    assert solver["solver_runtime_s"] is None


def test_version_when_available(monkeypatch):
    package = SimpleNamespace(gurobi=SimpleNamespace(version=lambda: (13, 0, 0)))
    monkeypatch.setattr("cryptolab.adapters.gurobi.import_module", lambda name: package)
    _, solver = extract(FakeModel(Status=2))
    assert solver["solver_version"] == "13.0.0"
