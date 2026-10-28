import hashlib
import sys

import pytest

from cryptolab import track_run
from cryptolab.artifacts import Artifacts


def test_add_file_copies_source_and_registers_hash(tmp_path, home, query):
    source = tmp_path / "source.txt"
    source.write_text("原始结果\n", encoding="utf-8")
    with track_run(project="p", experiment="e") as run:
        copied = run.add_file(source, name="nested/result.txt")
        assert copied.read_bytes() == source.read_bytes()
        assert query("SELECT * FROM artifacts")
    assert source.exists()
    row = query("SELECT * FROM artifacts WHERE original_name='source.txt'")[0]
    assert row["relative_path"] == f"runs/{run.id}/nested/result.txt"
    assert row["size_bytes"] == len(source.read_bytes())
    assert row["sha256"] == hashlib.sha256(source.read_bytes()).hexdigest()
    assert (home / row["relative_path"]).is_file()


def test_output_files_registered_on_exit_and_missing_files_skipped(query):
    with track_run(project="p", experiment="e") as run:
        path = run.output_path("nested/result.txt")
        assert path == run.output_path("nested/result.txt")
        assert not path.exists()
        path.write_text("result", encoding="utf-8")
        run.output_path("never-written.txt")
        assert not query("SELECT * FROM artifacts")
    assert {row["original_name"] for row in query("SELECT * FROM artifacts")} == {"run.log", "result.txt"}


@pytest.mark.parametrize("name", ["../escape", "/tmp/escape", "a/../../escape", "C:/escape",
                                       "a\\escape", "", ".", "\x00bad", "run.log", "stdout.log", "traceback.txt"])
def test_unsafe_and_reserved_output_names_rejected(name):
    with pytest.raises(ValueError):
        with track_run(project="p", experiment="e") as run:
            run.output_path(name)


def test_same_name_copy_does_not_overwrite(tmp_path, query):
    source = tmp_path / "source.txt"
    source.write_text("first")
    with pytest.raises(FileExistsError):
        with track_run(project="p", experiment="e") as run:
            target = run.add_file(source)
            source.write_text("second")
            run.add_file(source)
    assert target.read_text() == "first"
    assert source.read_text() == "second"
    assert len(query("SELECT * FROM artifacts WHERE original_name='source.txt'")) == 1


def test_missing_source_is_an_error():
    with pytest.raises(FileNotFoundError):
        with track_run(project="p", experiment="e") as run:
            run.add_file("this-source-does-not-exist")


def test_symlink_escape_rejected_at_request(tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    with pytest.raises(ValueError, match="Symlinks"):
        with track_run(project="p", experiment="e") as run:
            path = run.output_path("escape/file.txt")
            path.parent.rmdir()
            path.parent.symlink_to(outside, target_is_directory=True)
            run.output_path("escape/other.txt")
    assert not list(outside.iterdir())


def test_replaced_output_symlink_rejected_at_finalization(tmp_path, query):
    outside = tmp_path / "secret.txt"
    outside.write_text("do not archive")
    with pytest.raises(ValueError, match="Symlinks"):
        with track_run(project="p", experiment="e") as run:
            run.output_path("result.txt").symlink_to(outside)
    assert not query("SELECT * FROM artifacts WHERE original_name='result.txt'")
    assert query("SELECT status FROM runs")[0]["status"] == "FAILED"


def test_copy_failure_does_not_publish_partial_file(tmp_path, monkeypatch, home, query):
    source = tmp_path / "input.txt"
    source.write_text("source")
    def fail(input_file, output):
        output.write(b"partial")
        raise OSError("copy failed")
    monkeypatch.setattr("cryptolab.artifacts.shutil.copyfileobj", fail)
    with pytest.raises(OSError, match="copy failed"):
        with track_run(project="p", experiment="e") as run:
            run.add_file(source)
    directory = home / "runs" / run.id
    assert not (directory / "input.txt").exists()
    assert not list(directory.glob(".copy-*"))
    assert source.read_text() == "source"
    assert not query("SELECT * FROM artifacts WHERE original_name='input.txt'")


def test_console_is_streamed_restored_and_log_printed_once(home, capsys, query):
    original_stdout, original_stderr = sys.stdout, sys.stderr
    with track_run(project="p", experiment="e", capture_console=True) as run:
        print("live stdout")
        print("live stderr", file=sys.stderr)
        run.log("specific log message")
        directory = home / "runs" / run.id
        assert (directory / "stdout.log").read_text() == "live stdout\n"
        assert (directory / "stderr.log").read_text() == "live stderr\n"
        assert "specific log message" in (directory / "run.log").read_text()
    assert sys.stdout is original_stdout and sys.stderr is original_stderr
    captured = capsys.readouterr()
    assert captured.out.count("specific log message") == 1
    assert "live stdout" in captured.out and "live stderr" in captured.err
    assert {row["kind"] for row in query("SELECT * FROM artifacts")} == {"stdout", "stderr", "other"}


def test_console_restores_after_exception(home):
    original = sys.stdout
    with pytest.raises(ValueError):
        with track_run(project="p", experiment="e", capture_console=True):
            raise ValueError("failure")
    assert sys.stdout is original
    with track_run(project="p", experiment="e", capture_console=True):
        print("next capture works")


@pytest.mark.parametrize("outer_capture,inner_capture", [(True, True), (True, False), (False, True)])
def test_overlapping_capture_is_rejected(outer_capture, inner_capture):
    with track_run(project="p", experiment="e", capture_console=outer_capture):
        with pytest.raises(RuntimeError, match="run.log"):
            with track_run(project="p", experiment="e", capture_console=inner_capture):
                pass


def test_explicit_logs_do_not_share_handlers(home, capsys):
    with track_run(project="p", experiment="e") as first:
        with track_run(project="p", experiment="e") as second:
            first.log("only-first")
            second.log("only-second", level="WARNING")
    first_log = (home / "runs" / first.id / "run.log").read_text()
    second_log = (home / "runs" / second.id / "run.log").read_text()
    assert "only-first" in first_log and "only-second" not in first_log
    assert "only-second" in second_log and "only-first" not in second_log
    output = capsys.readouterr().out
    assert output.count("only-first") == output.count("only-second") == 1


def test_setup_failure_marks_run_failed_and_releases_capture(monkeypatch, query):
    original = Artifacts.output_path
    def fail(*args, **kwargs):
        raise OSError("cannot create logs")
    monkeypatch.setattr(Artifacts, "output_path", fail)
    with pytest.raises(OSError, match="cannot create logs"):
        with track_run(project="p", experiment="e", capture_console=True):
            pass
    assert query("SELECT status FROM runs")[0]["status"] == "FAILED"
    monkeypatch.setattr(Artifacts, "output_path", original)
    with track_run(project="p", experiment="e", capture_console=True):
        pass


def test_log_file_write_error_propagates_and_marks_failed(monkeypatch, query, capsys):
    with pytest.raises(OSError, match="disk full"):
        with track_run(project="p", experiment="e") as run:
            # Exercise the real logging handler: standard logging normally swallows this.
            stream = run._logger.handlers[0].stream
            def fail(message):
                raise OSError("disk full")
            monkeypatch.setattr(stream, "write", fail)
            run.log("must not disappear")
    assert query("SELECT status FROM runs")[0]["status"] == "FAILED"
    assert "disk full" in capsys.readouterr().err
