import socket
import pytest


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    connect = socket.socket.connect
    connect_ex = socket.socket.connect_ex
    def local_only(original):
        def guarded(sock, address):
            # Windows asyncio creates an internal loopback socketpair.
            if isinstance(address, tuple) and address[0] in {"127.0.0.1", "::1"}:
                return original(sock, address)
            raise AssertionError("Tests must not access external services")
        return guarded
    def blocked(*args, **kwargs):
        raise AssertionError("Tests must not access external services")
    monkeypatch.setattr(socket.socket, "connect", local_only(connect))
    monkeypatch.setattr(socket.socket, "connect_ex", local_only(connect_ex))
    monkeypatch.setattr(socket, "create_connection", blocked)
