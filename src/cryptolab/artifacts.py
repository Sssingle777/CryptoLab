"""Safe, per-run files with explicit registration and streaming checksums."""

import hashlib
from contextlib import contextmanager
import os
from pathlib import Path, PureWindowsPath
import shutil
import stat
import tempfile
from uuid import UUID

from .database import Database


RESERVED_NAMES = {"run.log", "stdout.log", "stderr.log", "traceback.txt"}


class Artifacts:
    def __init__(self, database: Database, run_id: str, *, create: bool = True) -> None:
        UUID(run_id)  # IDs used in paths must be single UUID components.
        self.database = database
        self.run_id = run_id
        self.root = database.home / "runs" / run_id
        if self.root.parent.is_symlink():
            raise ValueError("The runs directory must not be a symlink")
        if create:
            self.root.mkdir(parents=True, exist_ok=False)
        self.files: dict[Path, tuple[str, str]] = {}

    def safe_path(self, name: str, *, internal: bool = False) -> Path:
        if not isinstance(name, str) or not name or "\x00" in name or "\\" in name:
            raise ValueError("Attachment name must be a nonempty relative path")
        relative = Path(name)
        if (relative.is_absolute() or PureWindowsPath(name).drive or
                ".." in relative.parts or not relative.parts):
            raise ValueError("Absolute paths and '..' are not allowed")
        if not internal and relative.parts[0] in RESERVED_NAMES:
            raise ValueError(f"{relative.parts[0]} is reserved for SDK logs")
        path = self.root / relative
        self._check_path(path)
        return path

    def _check_path(self, path: Path) -> None:
        for part in (path, *path.parents):
            if part.is_symlink():
                raise ValueError("Symlinks are not allowed in Run attachment paths")
            if part == self.database.home:
                break
        if not path.resolve().is_relative_to(self.root.resolve()):
            raise ValueError("Attachment path escapes the Run directory")

    @staticmethod
    def _kind(path: Path) -> str:
        if path.name in {"run.log", "traceback.txt"}:
            return "other"
        if path.name == "stdout.log":
            return "stdout"
        if path.name == "stderr.log":
            return "stderr"
        if path.name == "gurobi.log":
            return "gurobi"
        return "model" if path.suffix.lower() in {".lp", ".mps"} else "result"

    def output_path(self, name: str, *, internal: bool = False) -> Path:
        path = self.safe_path(name, internal=internal)
        path.parent.mkdir(parents=True, exist_ok=True)
        kind = "other" if internal and path.name in {"run.log", "traceback.txt"} else self._kind(path)
        self.files.setdefault(path, (kind, path.name))
        return path

    def add_file(self, source: str | os.PathLike[str], name: str | None = None,
                 *, register: bool = True, allow_reserved: bool = False) -> Path:
        source_path = Path(source)
        if not source_path.is_file():
            raise FileNotFoundError(f"Attachment is not an existing regular file: {source_path}")
        target = self.safe_path(source_path.name if name is None else name, internal=allow_reserved)
        if target.exists() or target in self.files:
            raise FileExistsError(f"Attachment already exists or is reserved: {target.name}")
        target.parent.mkdir(parents=True, exist_ok=True)
        # A hard link publishes the completed temporary file without overwriting
        # a file created meanwhile. The temporary name is always removed.
        descriptor, temporary_name = tempfile.mkstemp(prefix=".copy-", dir=target.parent)
        temporary = Path(temporary_name)
        try:
            with os.fdopen(descriptor, "wb") as output, source_path.open("rb") as input_file:
                shutil.copyfileobj(input_file, output)
            self._check_path(target)
            os.link(temporary, target)
        finally:
            temporary.unlink(missing_ok=True)
        self.files[target] = (self._kind(target), source_path.name)
        if register:
            self.register(target)
        return target

    @contextmanager
    def open_file(self, name: str):
        """Read only regular files using no-follow directory-relative opens.

        Directory descriptors prevent a symlink swap in an ancestor from turning
        a preview/download into a read outside this Run. macOS/Linux support this.
        """
        path = self.safe_path(name, internal=True)
        descriptors = []
        file = None
        try:
            directory = os.open(self.database.home, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
            descriptors.append(directory)
            parts = path.relative_to(self.database.home).parts
            for component in parts[:-1]:
                directory = os.open(component, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=directory)
                descriptors.append(directory)
            descriptor = os.open(parts[-1], os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory)
            file = os.fdopen(descriptor, "rb")
            info = os.fstat(file.fileno())
            if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
                raise ValueError("Only regular, non-hard-linked Run files can be read")
            yield file
        finally:
            if file is not None:
                file.close()
            for descriptor in reversed(descriptors):
                os.close(descriptor)

    @staticmethod
    def signature(info) -> tuple:
        return info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns

    def inspect(self, name: str) -> dict:
        path = self.safe_path(name, internal=True)
        with self.open_file(name) as file:
            before = os.fstat(file.fileno())
            digest = hashlib.sha256()
            while chunk := file.read(1024 * 1024):
                digest.update(chunk)
            if self.signature(before) != self.signature(os.fstat(file.fileno())):
                raise ValueError("File changed while reading; retry when it is stable")
        return {"name": path.relative_to(self.root).as_posix(),
                "relative_path": path.relative_to(self.database.home).as_posix(),
                "size_bytes": before.st_size, "sha256": digest.hexdigest(),
                "signature": self.signature(before), "kind": self._kind(path)}

    def register(self, path: Path) -> None:
        self._check_path(path)
        if not path.exists():
            return  # output_path may have been requested but never written.
        if not path.is_file():
            raise ValueError(f"Attachment is not a regular file: {path}")
        digest = hashlib.sha256()
        size = 0
        with path.open("rb") as file:
            while chunk := file.read(1024 * 1024):
                size += len(chunk)
                digest.update(chunk)
        kind, original_name = self.files[path]
        self.database.save_artifact(
            self.run_id, kind, original_name,
            path.relative_to(self.database.home).as_posix(), size, digest.hexdigest(),
        )
