import socket
def deny(*a, **kw):
    raise AssertionError("DA09_OFFLINE_NETWORK_FORBIDDEN")
socket.socket.connect = deny
socket.socket.connect_ex = deny
socket.create_connection = deny
