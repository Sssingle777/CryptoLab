"""Standard-library formatting/export helpers shared by the UI and its tests."""

import csv
from datetime import date, datetime, time, timedelta, timezone
import io
import json
from zoneinfo import ZoneInfo


def local_time(value: str | None, zone: str) -> str:
    return "" if value is None else datetime.fromisoformat(value).astimezone(ZoneInfo(zone)).isoformat(sep=" ")


def input_time(value: str, zone: str) -> str | None:
    if not value.strip():
        return None
    parsed = datetime.fromisoformat(value.strip())
    if parsed.tzinfo is None:
        local = parsed.replace(tzinfo=ZoneInfo(zone))
        # Do not silently choose a nonexistent or ambiguous DST wall-clock time.
        if local.astimezone(timezone.utc).astimezone(ZoneInfo(zone)).replace(tzinfo=None) != parsed:
            raise ValueError("This local time does not exist; enter an explicit UTC offset")
        if local.utcoffset() != local.replace(fold=1).utcoffset():
            raise ValueError("This local time is ambiguous; enter an explicit UTC offset")
        parsed = local
    return parsed.astimezone(timezone.utc).isoformat(timespec="microseconds")


def date_bounds(start: date, end: date, zone: str) -> tuple[str, str]:
    if end < start:
        raise ValueError("End date must not precede start date")
    return (datetime.combine(start, time.min, ZoneInfo(zone)).astimezone(timezone.utc).isoformat(),
            datetime.combine(end + timedelta(days=1), time.min, ZoneInfo(zone)).astimezone(timezone.utc).isoformat())


EXPORT_FIELDS = ("id", "project_name", "experiment_name", "batch_id", "batch_name", "source",
                 "status", "display_status", "solver_name", "solver_status", "solver_version",
                 "started_at", "finished_at", "duration_s", "solver_runtime_s", "created_at",
                 "updated_at", "last_heartbeat_at", "deleted_at", "params", "metrics", "notes")


def export_runs(rows: list[dict], format: str) -> bytes:
    records = [{key: row.get(key) for key in EXPORT_FIELDS} for row in rows]
    if format == "json":
        return json.dumps(records, ensure_ascii=False, allow_nan=False, indent=2).encode("utf-8")
    if format != "csv":
        raise ValueError("format must be csv or json")
    output = io.StringIO(newline="")
    writer = csv.DictWriter(output, fieldnames=EXPORT_FIELDS)
    writer.writeheader()
    for record in records:
        for key, value in record.items():
            if isinstance(value, (dict, list)):
                record[key] = json.dumps(value, ensure_ascii=False, allow_nan=False)
            elif isinstance(value, str) and value.lstrip().startswith(("=", "+", "-", "@")):
                record[key] = "'" + value  # Prevent spreadsheet formula execution.
        writer.writerow(record)
    return output.getvalue().encode("utf-8-sig")
