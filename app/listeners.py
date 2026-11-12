"""Explicit separate IPv4/IPv6 sockets, independent of OS dual-stack defaults."""
import socket
import sys


def bind_listeners(host, port, dual_stack=False):
    hosts = ['0.0.0.0', '::'] if dual_stack and host in ('0.0.0.0', '::') else [host]
    sockets = []
    try:
        for address in hosts:
            family = socket.AF_INET6 if ':' in address else socket.AF_INET
            sock = socket.socket(family, socket.SOCK_STREAM)
            sockets.append(sock)
            if sys.platform == 'win32':
                sock.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
            if family == socket.AF_INET6:
                sock.setsockopt(socket.IPPROTO_IPV6, socket.IPV6_V6ONLY, 1)
            sock.bind((address, port))
            # Keep both families on the same port even for ephemeral test listeners.
            port = sock.getsockname()[1]
            sock.listen(128)
            sock.setblocking(False)
        return sockets
    except OSError:
        for sock in sockets:
            sock.close()
        raise
