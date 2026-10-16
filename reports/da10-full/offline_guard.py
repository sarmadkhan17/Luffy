"""Pytest-only offline transport guard for bounded DA-10 regression runs."""
import socket

def pytest_sessionstart(session):
    def denied(*args, **kwargs):
        raise AssertionError('DA10 regression attempted networking')
    socket.socket.connect = denied
    socket.create_connection = denied
