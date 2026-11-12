import time
import threading
import httpx
import sqlite3
from types import SimpleNamespace

import pytest

from app.database import Database
from app.geolocation import GeoLocator
from app.monitor import Monitor
from conftest import link


@pytest.mark.parametrize('ip,label', [
    ('127.0.0.1', '本机回环'), ('::ffff:127.0.0.1', '本机回环'),
    ('192.168.1.2', '内网 / 非公网地址'), ('100.100.1.2', 'Tailscale / 共享地址段'),
    ('fd7a:115c:a1e0::1', 'Tailscale 内网'), ('fe80::1%12', '链路本地地址'),
    ('invalid', '未知 IP'), ('8.8.8.8', '属地查询已关闭'),
])
def test_address_classes(ip, label):
    assert GeoLocator(False).lookup(ip) == label


def test_background_lookup_cache_and_retry(monkeypatch):
    calls = []
    started, release = threading.Event(), threading.Event()
    def fetch(ip):
        calls.append(ip)
        started.set()
        release.wait(2)
        return '中国–新疆–乌鲁木齐 移动'
    geo = GeoLocator()
    monkeypatch.setattr(geo, '_fetch', fetch)
    try:
        assert geo.lookup('39.144.208.221') == '属地查询中…'
        assert started.wait(1)
        assert geo.lookup('39.144.208.221') == '属地查询中…'
        assert geo.lookup('100.100.1.1') == 'Tailscale / 共享地址段'
        release.set()
        geo.jobs.join()
        assert geo.lookup('39.144.208.221') == '中国–新疆–乌鲁木齐 移动'
        assert len(calls) == 1
        def failure(ip):
            raise RuntimeError('offline')
        monkeypatch.setattr(geo, '_fetch', failure)
        geo.cache['39.144.208.221'] = ('expired', 0)
        assert geo.lookup('39.144.208.221') == '属地查询中…'
        geo.jobs.join()
        assert geo.lookup('39.144.208.221') == '属地查询失败（稍后重试）'
        assert geo.cache['39.144.208.221'][1] > time.monotonic()
    finally:
        release.set()
        geo.close()
    assert not geo.worker.is_alive()


@pytest.mark.parametrize('body,success', [
    ({'code': 0, 'data': {'location': '中国–新疆–乌鲁木齐 移动'}}, True),
    ({'code': 1}, False), ({'code': 0, 'data': {}}, False),
])
def test_api_contract(monkeypatch, body, success):
    real_client = httpx.Client
    def handler(request):
        assert request.url.scheme == 'https'
        assert request.url.params['ip'] == '39.144.208.221'
        assert request.url.params['type'] == 'json'
        assert request.headers['User-Agent'] == 'HTTP-DISTRIBUTE/1.0'
        return httpx.Response(200, json=body)
    monkeypatch.setattr('app.geolocation.httpx.Client', lambda **kw: real_client(transport=httpx.MockTransport(handler), **kw))
    geo = GeoLocator()
    if success:
        assert geo._fetch('39.144.208.221') == '中国–新疆–乌鲁木齐 移动'
    else:
        with pytest.raises(ValueError):
            geo._fetch('39.144.208.221')


def test_monotonic_average_and_finish_delay(tmp_path, monkeypatch):
    clock = {'mono': 10, 'wall': 100}
    monkeypatch.setattr('app.monitor.time', SimpleNamespace(
        perf_counter=lambda: clock['mono'], time=lambda: clock['wall']))
    monitor = Monitor(Database(tmp_path), 10, GeoLocator())
    key = monitor.start({'id': 1}, 'file.bin', '127.0.0.1', 'GET')
    clock['mono'] = 12
    monitor.sent(key, 1024)
    row = monitor.snapshot()['active'][0]
    assert row['average_speed'] == 512
    assert row['location'] == '本机回环'
    clock.update(mono=14, wall=90)
    monitor.stop(key)
    clock['mono'] = 20
    monitor.finish(key, 206, 'interrupted')
    row = monitor.history()[0]
    assert row['duration'] == 4
    assert row['average_speed'] == 256
    assert not row['duration_estimated']
    assert monitor.snapshot()['active_count'] == 0


def test_v1_migration_preserves_history(tmp_path):
    with sqlite3.connect(tmp_path / 'files.db') as db:
        db.executescript('''CREATE TABLE requests(id INTEGER PRIMARY KEY, link_id INTEGER,
            started REAL, ended REAL, ip TEXT, filename TEXT, method TEXT, status INTEGER,
            bytes INTEGER, outcome TEXT);
            INSERT INTO requests VALUES(1,1,100,102,'8.8.8.8','old.bin','GET',200,2048,'complete');
            PRAGMA user_version=1;''')
    for _ in range(2):
        database = Database(tmp_path)
        row = Monitor(database, 10).history()[0]
        assert row['filename'] == 'old.bin'
        assert row['duration_estimated'] and row['average_speed'] == 1024
        with database.connect() as db:
            assert db.execute('PRAGMA user_version').fetchone()[0] == 3


def test_download_api_statistics(env):
    ctx, admin, download, root, file = env
    item = link(admin, file)
    url = item['url']
    assert download.get(url, headers={'range': 'bytes=0-1023'}).status_code == 206
    row = admin.get('/api/requests').json()[0]
    assert row['bytes'] == 1024 and row['duration'] > 0
    assert row['average_speed'] == pytest.approx(1024 / row['duration'])
    assert 'location' in row
    assert download.head(url).status_code == 200
    assert admin.get('/api/requests').json()[0]['average_speed'] is None
    assert download.get(url, headers={'range': 'bytes=99999999-'}).status_code == 416
    assert admin.get('/api/requests').json()[0]['average_speed'] is None
    assert admin.get('/api/monitor').json()['geoip']['state'] == 'online'
