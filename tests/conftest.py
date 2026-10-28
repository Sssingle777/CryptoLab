import sqlite3

import pytest


@pytest.fixture(autouse=True)
def home(tmp_path, monkeypatch):
    path = tmp_path / "data"
    monkeypatch.setenv("CRYPTOLAB_HOME", str(path))
    return path


@pytest.fixture
def query(home):
    def read(sql, params=()):
        connection = sqlite3.connect(home / "cryptolab.db")
        connection.row_factory = sqlite3.Row
        try:
            return [dict(row) for row in connection.execute(sql, params).fetchall()]
        finally:
            connection.close()
    return read
