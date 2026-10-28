"""Read-only file inspection and explicitly requested, audited attachment changes."""

from contextlib import contextmanager
import io
import os
from pathlib import Path
from uuid import uuid4

from .artifacts import Artifacts
from .database import Database, audit_change, utc_now
from .records import require_reason, require_run


LOG_NAMES = ("run.log", "stdout.log", "stderr.log", "gurobi.log", "traceback.txt")


def scan_unregistered_files(run_id: str) -> dict:
    """No writes, mkdir, or registration. Hash ordinary stable files in chunks."""
    database = Database()
    def read(connection):
        require_run(connection, run_id, allow_deleted=True)
        return {row[0] for row in connection.execute("SELECT relative_path FROM artifacts WHERE run_id=?", (run_id,))}
    registered = database.read(read)
    files = Artifacts(database, run_id, create=False)
    candidates, rejected = [], []
    files._check_path(files.root)
    if not files.root.exists():
        return {"files": [], "rejected": []}
    for directory, directories, names in os.walk(files.root, followlinks=False):
        for name in directories[:]:
            path = Path(directory) / name
            if path.is_symlink():
                rejected.append({"name": path.relative_to(files.root).as_posix(), "reason": "symlink directory"})
                directories.remove(name)
        for name in names:
            path = Path(directory) / name
            if path.relative_to(database.home).as_posix() in registered:
                continue
            relative = path.relative_to(files.root).as_posix()
            if any(part.startswith(".copy-") for part in path.relative_to(files.root).parts):
                rejected.append({"name": relative, "reason": "incomplete copy temporary file"})
                continue
            try:
                candidates.append(files.inspect(relative))
            except (OSError, ValueError) as error:
                rejected.append({"name": relative, "reason": str(error)})
    return {"files": candidates, "rejected": rejected}


def _register(database: Database, files: Artifacts, metadata: dict, reason: str) -> str:
    identifier = str(uuid4())
    def write(connection):
        require_run(connection, files.run_id, finished=True)
        # Hashing is already complete, outside the write transaction. Reopen the
        # exact path and check identity/size/timestamps before committing metadata.
        with files.open_file(metadata["name"]) as file:
            if files.signature(os.fstat(file.fileno())) != metadata["signature"]:
                raise ValueError("File changed; scan again before registration")
            if connection.execute("SELECT 1 FROM artifacts WHERE relative_path=?", (metadata["relative_path"],)).fetchone():
                raise FileExistsError("File is already registered")
            now = utc_now()
            connection.execute(
                """INSERT INTO artifacts(id,run_id,kind,original_name,relative_path,size_bytes,sha256,created_at)
                   VALUES (?,?,?,?,?,?,?,?)""",
                (identifier, files.run_id, metadata["kind"], metadata.get("original_name", Path(metadata["name"]).name),
                 metadata["relative_path"], metadata["size_bytes"], metadata["sha256"], now),
            )
            connection.execute("UPDATE runs SET updated_at=? WHERE id=?", (now, files.run_id))
            audit_change(connection, files.run_id, "artifact_registered", None,
                         {"id": identifier, "relative_path": metadata["relative_path"], "sha256": metadata["sha256"]}, reason, now)
    database._write(write)
    return identifier


def register_existing_file(run_id: str, name: str, *, expected_sha256: str,
                           expected_size: int, reason: str) -> str:
    require_reason(reason)
    database = Database()
    database.read(lambda connection: require_run(connection, run_id, finished=True))
    files = Artifacts(database, run_id, create=False)
    if any(part.startswith(".copy-") for part in Path(name).parts):
        raise ValueError("Incomplete copy temporary files cannot be registered")
    metadata = files.inspect(name)
    if metadata["sha256"] != expected_sha256 or metadata["size_bytes"] != expected_size:
        raise ValueError("File content changed; scan again before registration")
    return _register(database, files, metadata, reason)


def add_attachment(run_id: str, path: str | os.PathLike, *, name: str | None = None,
                   reason: str = "手动添加附件") -> str:
    require_reason(reason)
    database = Database()
    database.read(lambda connection: require_run(connection, run_id, finished=True))
    files = Artifacts(database, run_id, create=False)
    target = files.add_file(path, name, register=False, allow_reserved=True)  # Existing logs still cannot be overwritten.
    metadata = files.inspect(target.relative_to(files.root).as_posix())
    metadata["original_name"] = Path(path).name
    # If metadata writing fails, preserve the copied file for explicit recovery.
    return _register(database, files, metadata, reason)


@contextmanager
def open_attachment(artifact_id: str):
    database = Database()
    row = database.read(lambda connection: connection.execute("SELECT * FROM artifacts WHERE id=?", (artifact_id,)).fetchone())
    if row is None:
        raise ValueError("Unknown artifact")
    files = Artifacts(database, row["run_id"], create=False)
    relative = Path(row["relative_path"])
    if relative.is_absolute() or ".." in relative.parts:
        raise ValueError("Unsafe registered attachment path")
    try:
        name = relative.relative_to(Path("runs") / row["run_id"]).as_posix()
    except ValueError:
        raise ValueError("Attachment belongs outside its Run directory") from None
    with files.open_file(name) as file:
        yield file


def available_logs(run_id: str) -> list[str]:
    database = Database()
    database.read(lambda connection: require_run(connection, run_id, allow_deleted=True))
    files = Artifacts(database, run_id, create=False)
    result = []
    for name in LOG_NAMES:
        try:
            with files.open_file(name):
                result.append(name)
        except FileNotFoundError:
            pass
    return result


@contextmanager
def open_log(run_id: str, name: str):
    if name not in LOG_NAMES:
        raise ValueError("Not a supported Run log name")
    database = Database()
    database.read(lambda connection: require_run(connection, run_id, allow_deleted=True))
    with Artifacts(database, run_id, create=False).open_file(name) as file:
        yield file


def log_tail(run_id: str, name: str, *, max_bytes: int = 256 * 1024, max_lines: int = 300) -> str:
    if not 0 < max_bytes <= 1024 * 1024 or not 0 < max_lines <= 1000:
        raise ValueError("Log preview limits are out of range")
    with open_log(run_id, name) as file:
        file.seek(0, os.SEEK_END)
        file.seek(max(0, file.tell() - max_bytes))
        text = file.read(max_bytes).decode("utf-8", errors="replace")
    return "\n".join(text.splitlines()[-max_lines:])


def search_log(run_id: str, name: str, term: str, *, max_matches: int = 200) -> dict:
    if not term or len(term) > 4096 or "\n" in term or not 0 < max_matches <= 1000:
        raise ValueError("Provide a single-line search term (up to 4096 characters) and a match limit between 1 and 1000")
    matches = []
    with open_log(run_id, name) as binary:
        text = io.TextIOWrapper(binary, encoding="utf-8", errors="replace")
        try:
            line = 1
            overlap = ""
            while segment := text.readline(64 * 1024):
                searchable = overlap + segment
                if term in searchable:
                    matches.append(f"{line}: {searchable.rstrip()}")
                    if len(matches) == max_matches:
                        return {"matches": matches, "limited": True}
                if segment.endswith("\n"):
                    line += 1
                    overlap = ""
                else:
                    # Find terms crossing a chunk boundary without loading an
                    # unbounded line. Overlap alone is shorter than the term.
                    overlap = searchable[-(len(term) - 1):] if len(term) > 1 else ""
        finally:
            text.detach()
    return {"matches": matches, "limited": False}
