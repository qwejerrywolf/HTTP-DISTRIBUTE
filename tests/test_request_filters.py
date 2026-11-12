import time

from fastapi.testclient import TestClient

from app.download import create_download
from app.geolocation import GeoLocator
from app.ip_rules import IPRules
from conftest import link


def record(db, ip, started, link_id=1):
    db.execute("INSERT INTO requests(link_id,started,ended,ip,filename,method,status,bytes,outcome,duration) VALUES (?,?,?,?,?,'GET',200,100,'complete',1)",
               (link_id, started, started + 1, ip, 'test.bin'))


def test_filters_search_before_pagination(env):
    ctx, admin, *_ = env
    ctx.geoip.enabled = False
    for i in range(205):
        record(ctx.db, '39.144.208.221', 1000 + i)
    record(ctx.db, '8.8.8.8', 999, 2)
    record(ctx.db, '8.8.8.8', 1205, 2)
    ctx.geoip._save('39.144.208.221', '中国 新疆 移动 IDC机房', 86400)
    ctx.geoip._save('8.8.8.8', '美国', 86400)
    result = admin.get('/api/requests/search', params={'start': 1000, 'end': 1205, 'ip': '39.144', 'location': 'idc'}).json()
    assert result['total'] == 205 and len(result['items']) == 100
    assert all(r['flagged'] for r in result['items'])
    last = admin.get('/api/requests/search', params={'location': '新疆', 'page': 3}).json()
    assert len(last['items']) == 5 and last['total'] == 205
    assert admin.get('/api/requests/search', params={'link_id': 2}).json()['total'] == 2
    assert admin.get('/api/requests/search', params={'location': '%'}).json()['total'] == 0
    assert admin.get('/api/requests/search', params={'start': 1205, 'end': 1206}).json()['total'] == 1
    assert admin.get('/api/requests/search', params={'start': 5, 'end': 4}).status_code == 400


def test_blocking_normalization_persistence_and_routes(env):
    ctx, admin, _, root, file = env
    url = link(admin, file)['url']
    folder = link(admin, root)['url']
    result = admin.post('/api/blocked-ips', json={'ip': '::ffff:39.144.208.221'})
    assert result.status_code == 200 and result.json()['ip'] == '39.144.208.221'
    assert IPRules(ctx.db).contains('39.144.208.221')
    with TestClient(create_download(ctx), client=('39.144.208.221', 1234)) as buyer:
        for method, address, headers in [('GET', url, {}), ('HEAD', url, {}), ('GET', url, {'Range': 'bytes=0-9'}), ('GET', folder, {})]:
            assert buyer.request(method, address, headers=headers).status_code == 403
        assert buyer.get(url, headers={'X-Forwarded-For': '8.8.8.8'}).status_code == 403
        assert admin.get('/api/monitor').status_code == 200
        assert admin.post('/api/blocked-ips', json={'ip': '39.144.208.221', 'blocked': False}).status_code == 200
        assert buyer.get(url).status_code == 200
    assert not IPRules(ctx.db).contains('39.144.208.221')
    assert admin.post('/api/blocked-ips', json={'ip': 'not-an-ip'}).status_code == 400
    assert admin.post('/api/blocked-ips', json={'ip': '192.168.0.0/24'}).status_code == 400
    assert admin.post('/api/blocked-ips', json={'ip': '2001:4860:4860::8888'}).status_code == 200
    assert ctx.ip_rules.contains('2001:4860:4860:0:0:0:0:8888')
    admin.headers['x-csrf-token'] = 'invalid'
    assert admin.post('/api/blocked-ips', json={'ip': '1.1.1.1'}).status_code == 403


def test_backfill_unseen_history_and_restart_cache(env, monkeypatch):
    ctx, admin, *_ = env
    record(ctx.db, '39.144.208.221', 100)
    for _ in range(105):
        record(ctx.db, '127.0.0.1', 200)
    calls = []
    def fetch(ip):
        calls.append(ip)
        return '新疆 IDC机房'
    monkeypatch.setattr(ctx.geoip, '_fetch', fetch)
    assert admin.get('/api/requests/search').json()['total'] == 106
    deadline = time.monotonic() + 4
    while time.monotonic() < deadline:
        if ctx.db.one('SELECT * FROM ip_locations WHERE ip=?', ('39.144.208.221',)):
            break
        time.sleep(.02)
    assert calls == ['39.144.208.221']
    result = admin.get('/api/requests/search', params={'location': 'idc'}).json()
    assert result['total'] == 1 and result['items'][0]['flagged']
    fresh = GeoLocator(True, ctx.db)
    try:
        assert fresh.lookup('39.144.208.221') == '新疆 IDC机房'
        assert fresh.worker is None
    finally:
        fresh.close()
