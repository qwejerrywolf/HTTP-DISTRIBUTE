"""Exercise actual IPv6 TCP downloads, without system HTTP proxies."""
import asyncio
import socket
import threading
import time
from urllib.parse import urlsplit

import httpx
import pytest
import uvicorn

from app.download import create_download
from app.links import public_url
from app.listeners import bind_listeners
from conftest import link


@pytest.mark.parametrize('host', ['::1', '0.0.0.0', '::'])
def test_ipv6_download_and_blocking(env, host):
    ctx, admin, _, root, file = env
    probe = socket.socket(socket.AF_INET6, socket.SOCK_STREAM)
    try:
        probe.bind(('::1', 0))
    except OSError:
        pytest.skip('IPv6 loopback unavailable')
    finally:
        probe.close()
    sockets = bind_listeners(host, 0, dual_stack=True)
    port = sockets[0].getsockname()[1]
    prefix = public_url(f'http://[::1]:{port}')
    ctx.db.set_setting('public_url', prefix)
    url = link(admin, file)['url']
    folder = link(admin, root)['url']
    assert urlsplit(url).hostname == '::1'
    server = uvicorn.Server(uvicorn.Config(create_download(ctx), log_level='critical',
        access_log=False, proxy_headers=False, timeout_graceful_shutdown=3))
    thread = threading.Thread(target=lambda: asyncio.run(server.serve(sockets=sockets)), daemon=True)
    thread.start()
    try:
        deadline = time.monotonic() + 10
        while not server.started:
            assert time.monotonic() < deadline
            time.sleep(.02)
        with httpx.Client(trust_env=False, timeout=5) as client:
            if host != '::1':
                v4url = url.replace('[::1]', '127.0.0.1')
                v4 = client.get(v4url)
                assert v4.status_code == 200 and v4.content == file.read_bytes()
                assert client.head(v4url).status_code == 200
                assert client.get(v4url, headers={'Range': 'bytes=0-1023'}).status_code == 206
                assert ctx.monitor.history()[0]['ip'] == '127.0.0.1'
                ctx.ip_rules.change('127.0.0.1', True)
                assert client.get(v4url).status_code == 403
                ctx.ip_rules.change('127.0.0.1', False)
            response = client.get(url)
            assert response.status_code == 200 and response.content == file.read_bytes()
            assert client.head(url).status_code == 200
            response = client.get(url, headers={'Range': 'bytes=0-1023'})
            assert response.status_code == 206 and len(response.content) == 1024
            assert client.get(folder).status_code == 200
            assert ctx.monitor.history()[0]['ip'] == '::1'
            ctx.ip_rules.change('0:0:0:0:0:0:0:1', True)
            assert client.get(url).status_code == 403
            ctx.ip_rules.change('::1', False)
            assert client.get(url).status_code == 200
    finally:
        server.should_exit = True
        thread.join(8)
        for sock in sockets:
            sock.close()


def test_dual_stack_conflict_releases_ipv4():
    occupied = bind_listeners('::1', 0)
    port = occupied[0].getsockname()[1]
    try:
        with pytest.raises(OSError):
            bind_listeners('0.0.0.0', port, dual_stack=True)
        released = bind_listeners('127.0.0.1', port)
        try:
            assert len(released) == 1
            assert released[0].getsockname()[0] == '127.0.0.1'
        finally:
            released[0].close()
    finally:
        occupied[0].close()
