import time
import pytest

@pytest.fixture
def paths(tmp_path, monkeypatch):
    import socket
    monkeypatch.setattr(socket.socket, "connect", lambda *a: pytest.fail("network forbidden"))
    now = int(time.time()*1000)//14_400_000*14_400_000+60_000
    return tmp_path/"attention.db", tmp_path/"investigation.db", now
