import hashlib
import json
import os
from pathlib import Path
import sqlite3

import pytest

from cryptolab import create_manual_run, soft_delete_run, track_run
from cryptolab.database import Database
from cryptolab.files import (add_attachment, available_logs, log_tail, open_attachment,
                             register_existing_file, scan_unregistered_files, search_log)
from cryptolab.records import get_run


def test_detection_is_read_only_then_registration_is_audited(home):
    run_id = create_manual_run(project="p", experiment="e")
    path = home / "runs" / run_id / "result.txt"
    path.write_text("existing result")
    before = get_run(run_id)
    candidate = scan_unregistered_files(run_id)["files"][0]
    assert get_run(run_id) == before
    assert candidate["sha256"] == hashlib.sha256(path.read_bytes()).hexdigest()
    artifact_id = register_existing_file(run_id, candidate["name"], expected_sha256=candidate["sha256"],
                                         expected_size=candidate["size_bytes"], reason="recover after crash")
    with open_attachment(artifact_id) as file:
        assert file.read() == b"existing result"
    after = get_run(run_id)
    assert after["changes"][-1]["field_name"] == "artifact_registered"
    assert after["updated_at"] > before["updated_at"]
    assert not scan_unregistered_files(run_id)["files"]
    with pytest.raises(FileExistsError):
        register_existing_file(run_id, candidate["name"], expected_sha256=candidate["sha256"],
                               expected_size=candidate["size_bytes"], reason="duplicate")


def test_changed_file_requires_new_scan(home):
    run_id = create_manual_run(project="p", experiment="e")
    path = home / "runs" / run_id / "result.txt"
    path.write_text("first")
    candidate = scan_unregistered_files(run_id)["files"][0]
    path.write_text("other")
    with pytest.raises(ValueError, match="changed"):
        register_existing_file(run_id, "result.txt", expected_sha256=candidate["sha256"], expected_size=5, reason="test")
    assert not get_run(run_id)["artifacts"]


def test_file_replaced_between_hash_and_commit_is_not_registered(home, monkeypatch):
    from cryptolab.artifacts import Artifacts
    run_id = create_manual_run(project="p", experiment="e")
    path = home / "runs" / run_id / "result.txt"
    path.write_text("first")
    candidate = scan_unregistered_files(run_id)["files"][0]
    original = Artifacts.inspect
    def inspect_then_replace(files, name):
        metadata = original(files, name)
        replacement = path.with_name("replacement.txt")
        replacement.write_text("other")
        replacement.replace(path)
        return metadata
    monkeypatch.setattr(Artifacts, "inspect", inspect_then_replace)
    with pytest.raises(ValueError, match="File changed"):
        register_existing_file(run_id, "result.txt", expected_sha256=candidate["sha256"], expected_size=5, reason="test")
    assert not get_run(run_id)["artifacts"]


def test_scan_rejects_symlinks_hardlinks_fifo_and_partial_copies(home, tmp_path):
    run_id = create_manual_run(project="p", experiment="e")
    directory = home / "runs" / run_id
    secret = tmp_path / "secret.txt"
    secret.write_text("outside")
    (directory / "link.txt").symlink_to(secret)
    (directory / "linked-dir").symlink_to(tmp_path, target_is_directory=True)
    os.link(secret, directory / "hard.txt")
    os.mkfifo(directory / "pipe")
    (directory / ".copy-incomplete").write_text("partial")
    scan = scan_unregistered_files(run_id)
    assert not scan["files"]
    assert len(scan["rejected"]) == 5
    assert secret.read_text() == "outside"


@pytest.mark.parametrize("name", ["../outside", "/tmp/outside", "C:/outside", "a\\b"])
def test_registration_rejects_unsafe_names(name):
    run_id = create_manual_run(project="p", experiment="e")
    with pytest.raises(ValueError):
        register_existing_file(run_id, name, expected_sha256="bad", expected_size=0, reason="test")


def test_active_and_deleted_runs_cannot_register_or_upload(home, tmp_path):
    source = tmp_path / "file.txt"
    source.write_text("test")
    with track_run(project="p", experiment="e") as run:
        run.output_path("result.txt").write_text("test")
        candidate = next(item for item in scan_unregistered_files(run.id)["files"] if item["name"] == "result.txt")
        with pytest.raises(ValueError, match="RUNNING"):
            register_existing_file(run.id, "result.txt", expected_sha256=candidate["sha256"], expected_size=4, reason="unsafe")
        with pytest.raises(ValueError, match="RUNNING"):
            add_attachment(run.id, source)
    soft_delete_run(run.id, reason="test")
    with pytest.raises(ValueError, match="Restore"):
        add_attachment(run.id, source)


def test_copy_and_audit_failure_preserves_source_and_recoverable_file(tmp_path, home):
    run_id = create_manual_run(project="p", experiment="e")
    source = tmp_path / "input.txt"
    source.write_text("source")
    with Database().connect() as connection:
        connection.execute("CREATE TRIGGER reject_attachment_audit BEFORE INSERT ON run_changes BEGIN SELECT RAISE(ABORT,'blocked'); END")
    with pytest.raises(sqlite3.IntegrityError):
        add_attachment(run_id, source)
    assert source.read_text() == "source"
    assert (home / "runs" / run_id / "input.txt").read_text() == "source"
    assert not get_run(run_id)["artifacts"]
    assert len(scan_unregistered_files(run_id)["files"]) == 1


def test_download_revalidates_database_path_and_symlink(tmp_path, home):
    run_id = create_manual_run(project="p", experiment="e")
    source = tmp_path / "input.txt"
    source.write_text("source")
    artifact_id = add_attachment(run_id, source)
    target = home / "runs" / run_id / "input.txt"
    target.unlink()
    target.symlink_to(source)
    with pytest.raises(ValueError, match="Symlinks"):
        with open_attachment(artifact_id):
            pass
    with Database().connect() as connection:
        connection.execute("UPDATE artifacts SET relative_path=? WHERE id=?", ("../input.txt", artifact_id))
    with pytest.raises(ValueError, match="Unsafe"):
        with open_attachment(artifact_id):
            pass


def test_logs_tail_search_and_live_unregistered_log(home):
    with track_run(project="p", experiment="e") as run:
        assert available_logs(run.id) == ["run.log"]
        run.log("visible before finalization")
        assert "visible before finalization" in log_tail(run.id, "run.log")
    path = home / "runs" / run.id / "gurobi.log"
    with path.open("w") as file:
        file.write("needle at beginning\n")
        file.write("x" * (2 * 1024 * 1024) + "\n")
        file.write("last line\n")
    tail = log_tail(run.id, "gurobi.log", max_bytes=100, max_lines=1)
    assert tail == "last line"
    assert search_log(run.id, "gurobi.log", "needle")["matches"] == ["1: needle at beginning"]
    assert len(search_log(run.id, "gurobi.log", "x", max_matches=2)["matches"]) == 2


def test_log_search_finds_term_crossing_large_line_chunks(home):
    run_id = create_manual_run(project="p", experiment="e")
    path = home / "runs" / run_id / "stdout.log"
    path.write_text("x" * (64 * 1024 - 2) + "needle\nnext line\n")
    result = search_log(run_id, "stdout.log", "needle")
    assert len(result["matches"]) == 1
    assert result["matches"][0].startswith("1:")
    assert "needle" in result["matches"][0]


def test_sdk_attachment_updates_modification_time(tmp_path):
    source = tmp_path / "input.txt"
    source.write_text("data")
    with track_run(project="p", experiment="e") as run:
        before = get_run(run.id)
        run.add_file(source)
        after = get_run(run.id)
        assert after["updated_at"] > before["updated_at"]
        assert after["last_heartbeat_at"] == before["last_heartbeat_at"]
