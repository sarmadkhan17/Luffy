"""Review-only guard inherited by spawned synthetic test workers."""
import os, socket, sqlite3
from pathlib import Path
from urllib.parse import unquote
ROOT = Path(__file__).resolve().parents[3]
_connect = sqlite3.connect

def connect(database, *args, **kwargs):
    raw = os.fspath(database)
    if raw != ':memory:' and not raw.startswith('file::memory:'):
        filename = unquote(raw.removeprefix('file:').split('?')[0])
        p = Path(filename).resolve()
        if not (p.is_relative_to(ROOT) or p.is_relative_to(Path('/tmp'))):
            raise RuntimeError('REVIEW_DB_BOUNDARY_REFUSED')
    return _connect(database, *args, **kwargs)
sqlite3.connect = connect
_original = socket.socket.connect

def offline(self, address):
    if self.family in (socket.AF_INET, socket.AF_INET6):
        raise RuntimeError('REVIEW_NETWORK_REFUSED')
    return _original(self, address)
socket.socket.connect = offline

def refuse(*args, **kwargs):
    raise RuntimeError('REVIEW_NETWORK_REFUSED')
socket.create_connection = refuse
