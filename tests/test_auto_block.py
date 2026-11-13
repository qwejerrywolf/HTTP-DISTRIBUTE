import time
from concurrent.futures import ThreadPoolExecutor
from contextlib import ExitStack

import httpx
import pytest
from fastapi.testclient import TestClient

from app.download import create_download
from app.ip_rules import IPRules
from conftest import link
from test_live import live


def test_policy_off_on_and_manual_release_survives_restart(env):
    ctx, admin, _, _, file = env
    assert admin.get('/api/ip-policy').json() == {'enabled': False, 'handled_count': 0}
    ctx.geoip._save('39.144.208.221', 'Alibaba Cloud', 86400)
    assert not ctx.ip_rules.contains('39.144.208.221')
    url = link(admin, file)['url']
    assert admin.put('/api/ip-policy', json={'enabled': True}).status_code == 200
    with TestClient(create_download(ctx), client=('39.144.208.221', 1234)) as buyer:
        assert buyer.get(url).status_code == 403
        assert admin.post('/api/blocked-ips', json={'ip': '39.144.208.221', 'blocked': False}).status_code == 200
        ctx.geoip._save('::ffff:39.144.208.221', 'IDC机房', 86400)
        assert buyer.get(url).status_code == 200
        assert admin.put('/api/ip-policy', json={'enabled': False}).status_code == 200
        assert admin.put('/api/ip-policy', json={'enabled': True}).status_code == 200
        assert buyer.get(url).status_code == 200
    restarted = IPRules(ctx.db)
    assert restarted.status() == {'enabled': True, 'handled_count': 1}
    assert not restarted.evaluate('::ffff:39.144.208.221', 'AWS')
    assert not restarted.contains('39.144.208.221')
    assert admin.put('/api/ip-policy', json={'enabled': 'false'}).status_code == 422
    admin.headers['x-csrf-token'] = 'wrong'
    assert admin.put('/api/ip-policy', json={'enabled': False}).status_code == 403


def test_concurrent_hits_cancel_once_and_disabled_preserves_blacklist(env):
    ctx, *_ = env
    rules = ctx.ip_rules
    rules.set_enabled(True)
    calls = []
    ticket = rules.attach('2001:4860::1234', lambda: calls.append('cancel'))
    try:
        with ThreadPoolExecutor(max_workers=8) as pool:
            results = list(pool.map(lambda _: rules.evaluate('2001:4860:0:0:0:0:0:1234', 'BGP节点'), range(20)))
        assert sum(results) == 1 and calls == ['cancel']
        assert rules.status()['handled_count'] == 1
        rules.set_enabled(False)
        assert rules.contains('2001:4860::1234')
        rules.change('2001:4860::1234', False)
        rules.set_enabled(True)
        assert not rules.evaluate('2001:4860::1234', 'cloud hosting')
        assert calls == ['cancel']
    finally:
        rules.detach(ticket)


def test_background_lookup_blocks_without_monitor_page(env, monkeypatch):
    ctx, admin, *_ = env
    ctx.set_auto_block(True)
    monkeypatch.setattr(ctx.geoip, '_fetch', lambda _: '数据中心节点')
    assert ctx.geoip.lookup('8.8.8.8') == '属地查询中…'
    ctx.geoip.jobs.join()
    assert ctx.ip_rules.contains('8.8.8.8')
    assert ctx.ip_rules.status()['handled_count'] == 1


def test_auto_block_interrupts_all_backpressured_downloads(live):
    ctx, root, _, (public, admin) = live
    large = root / 'auto-block.bin'
    with large.open('wb') as output:
        output.truncate(64 * 1024 * 1024)
    url = public + '/d/' + ctx.links.create(str(large))['url'].rsplit('/', 1)[1]
    with httpx.Client(timeout=5, trust_env=False) as client, ExitStack() as stack:
        responses = [stack.enter_context(client.stream('GET', url)) for _ in range(2)]
        assert all(r.status_code == 200 for r in responses)
        assert ctx.monitor.snapshot()['active_count'] == 2
        ctx.set_auto_block(True)
        ctx.geoip._save('127.0.0.1', 'Cloudflare IDC机房', 86400)
        deadline = time.monotonic() + 3
        while ctx.monitor.snapshot()['active_count']:
            assert time.monotonic() < deadline, '后台发送被阻塞时仍必须立即取消'
            time.sleep(.02)
        for response in responses:
            with pytest.raises(httpx.RemoteProtocolError):
                response.read()
        rows = ctx.db.all('SELECT bytes,outcome FROM requests')
        assert len(rows) == 2
        assert all(r['outcome'] == 'interrupted' and r['bytes'] < large.stat().st_size for r in rows)
        assert client.get(admin + '/').status_code == 200
        assert client.get(url).status_code == 403
        with large.open('r+b'):
            pass  # Cancellation released Windows read handles.
        ctx.ip_rules.change('127.0.0.1', False)
        assert client.get(url, headers={'Range': 'bytes=0-99'}).status_code == 206
        assert ctx.ip_rules.status()['handled_count'] == 1
