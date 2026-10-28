import csv
from datetime import date
import io
import json

import pytest

from cryptolab import create_manual_run
from cryptolab.presentation import date_bounds, export_runs, input_time, local_time
from cryptolab.records import list_runs


def test_local_display_and_local_day_filter_boundaries():
    assert input_time("2025-01-02 00:30:00", "Asia/Shanghai") == "2025-01-01T16:30:00.000000+00:00"
    assert local_time("2025-01-01T16:30:00+00:00", "Asia/Shanghai").startswith("2025-01-02 00:30:00")
    start, end = date_bounds(date(2025, 1, 2), date(2025, 1, 2), "Asia/Shanghai")
    assert start == "2025-01-01T16:00:00+00:00" and end == "2025-01-02T16:00:00+00:00"
    assert input_time("", "Asia/Shanghai") is None


@pytest.mark.parametrize("text", ["2025-03-09 02:30:00", "2025-11-02 01:30:00"])
def test_ambiguous_or_nonexistent_dst_time_requires_offset(text):
    with pytest.raises(ValueError, match="explicit UTC offset"):
        input_time(text, "America/New_York")


def test_exports_preserve_types_utc_and_neutralize_csv_formulas():
    create_manual_run(project="=formula", experiment="e", params={"rounds": 8}, metrics={"sat": True},
                      started_at="2025-01-01T00:00:00+00:00")
    rows = list_runs()
    exported = json.loads(export_runs(rows, "json"))
    assert exported[0]["project_name"] == "=formula"
    assert exported[0]["metrics"]["sat"] is True
    assert exported[0]["started_at"].endswith("+00:00")
    csv_rows = list(csv.DictReader(io.StringIO(export_runs(rows, "csv").decode("utf-8-sig"))))
    assert csv_rows[0]["project_name"] == "'=formula"
    assert json.loads(csv_rows[0]["params"]) == {"rounds": 8}
    assert json.loads(export_runs([], "json")) == []
