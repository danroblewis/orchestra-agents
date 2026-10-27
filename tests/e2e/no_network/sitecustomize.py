"""Every Python process started with this directory on PYTHONPATH loses the network: a connection
or a name lookup that leaves this machine is refused and written to NO_NETWORK_LOG, so a test can
assert that nothing tried. Loopback and Unix sockets (the bus, the web UI, the control endpoint)
still work. Each process notes that the guard is in it, so the test can see it was in force."""

import ipaddress
import os
import socket
import sys

LOG = os.environ.get("NO_NETWORK_LOG")


def _note(line: str) -> None:
    with open(LOG, "a", encoding="utf-8") as f:
        f.write(f"{os.getpid()} {line}\n")


def _local(host) -> bool:
    if host in (None, "", "localhost") or str(host).endswith(".localhost"):
        return True
    try:
        return ipaddress.ip_address(str(host).split("%")[0]).is_loopback
    except ValueError:
        return False


if LOG:
    _note("guarded " + " ".join(getattr(sys, "orig_argv", sys.argv)).replace("\n", " ")[:300])
    _connect, _connect_ex, _getaddrinfo = socket.socket.connect, socket.socket.connect_ex, socket.getaddrinfo

    def _check(sock, address):
        if sock.family in (socket.AF_INET, socket.AF_INET6) and not _local(address[0]):
            _note(f"refused a connection to {address!r}")
            raise OSError(f"no network in this test: {address!r}")

    def connect(self, address):
        _check(self, address)
        return _connect(self, address)

    def connect_ex(self, address):
        _check(self, address)
        return _connect_ex(self, address)

    def getaddrinfo(host, *args, **kwargs):
        if not _local(host.decode() if isinstance(host, bytes) else host):
            _note(f"refused a lookup of {host!r}")
            raise socket.gaierror(f"no network in this test: {host!r}")
        return _getaddrinfo(host, *args, **kwargs)

    socket.socket.connect, socket.socket.connect_ex, socket.getaddrinfo = connect, connect_ex, getaddrinfo
