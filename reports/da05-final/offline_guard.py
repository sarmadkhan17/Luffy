"""Pytest plugin denying real socket connections for the entire selection."""
import socket


def denied(*args, **kwargs):
    raise AssertionError('DA05 offline verification: network forbidden')


socket.socket.connect = denied
socket.socket.connect_ex = denied
socket.create_connection = denied
