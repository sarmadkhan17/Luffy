"""Deny real HTTP/socket I/O and fail the campaign if a fixture attempts it."""
import socket
import sqlite3
import requests
import pytest
import traceback
from pathlib import Path

_attempts=[]
_store_attempts=[]


def _deny(*args, **kwargs):
    _attempts.append(tuple((Path(x.filename).name,x.name) for x in traceback.extract_stack() if '/tests/' in x.filename))
    raise AssertionError('STAGE1_OFFLINE_NETWORK_DENIED')


def pytest_sessionstart(session):
    requests.sessions.Session.request = _deny
    # Temporary Unix IPC is exercised offline by Owner OS tests. IP sockets
    # and production IPC remain forbidden, including unknown/abstract paths.
    import tempfile
    for name in ('connect', 'connect_ex'):
        original = getattr(socket.socket, name)
        def local_only(sock, address, *args, _original=original, **kwargs):
            if sock.family == socket.AF_UNIX:
                raw = address.decode() if isinstance(address, bytes) else address
                if isinstance(raw, str) and raw and not raw.startswith('\0'):
                    path = Path(raw).resolve()
                    if path.is_relative_to(Path(tempfile.gettempdir()).resolve()):
                        return _original(sock, address, *args, **kwargs)
            return _deny(sock, address, *args, **kwargs)
        setattr(socket.socket, name, local_only)
    socket.getaddrinfo = _deny
    original_connect=sqlite3.connect
    production_data=Path(__file__).resolve().parents[1]/'data'
    def connect(database,*args,**kwargs):
        from urllib.parse import unquote
        raw=str(database)
        filename=unquote(raw.removeprefix('file:').split('?')[0])
        if filename != ':memory:' and Path(filename).resolve().is_relative_to(production_data):
            _store_attempts.append(Path(filename).name)
            raise AssertionError('STAGE1_PRODUCTION_STORE_DENIED')
        return original_connect(database,*args,**kwargs)
    sqlite3.connect=connect


def pytest_sessionfinish(session, exitstatus):
    if _attempts:
        session.exitstatus=pytest.ExitCode.TESTS_FAILED
        print(f'\nOFFLINE GUARD: {len(_attempts)} network attempts denied; campaign invalid. Callers: {set(_attempts)}')
    else:
        print('\nOFFLINE GUARD: 0 network attempts.')
    if _store_attempts:
        session.exitstatus=pytest.ExitCode.TESTS_FAILED
        print(f'PRODUCTION STORE GUARD: {len(_store_attempts)} denied attempts; campaign invalid: {sorted(set(_store_attempts))}')
    else:
        print('PRODUCTION STORE GUARD: 0 attempts.')
