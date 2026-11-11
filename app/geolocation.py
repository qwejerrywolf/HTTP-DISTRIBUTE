"""Cached, background IP geolocation via the zxinc HTTPS API."""
import ipaddress
import queue
import threading
import time
from collections import OrderedDict

import httpx


class GeoLocator:
    API_URL = 'https://ip.zxinc.org/api.php'

    def __init__(self, enabled=True):
        self.enabled = enabled
        self.lock = threading.RLock()
        self.cache = OrderedDict()
        self.pending = set()
        self.jobs = queue.Queue(maxsize=128)
        self.closed = threading.Event()
        self.worker = None

    def status(self):
        return dict(state='online' if self.enabled else 'disabled',
                    message='在线属地查询已启用（公网 IP 将发送至 ip.zxinc.org）' if self.enabled else 'IP 属地在线查询已关闭',
                    provider='ip.zxinc.org', url=self.API_URL)

    def lookup(self, value):
        try:
            ip = ipaddress.ip_address(value.split('%', 1)[0])
            if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped:
                ip = ip.ipv4_mapped
        except ValueError:
            return '未知 IP'
        if ip.is_loopback:
            return '本机回环'
        if (ip.version == 4 and ip in ipaddress.ip_network('100.64.0.0/10')) or (ip.version == 6 and ip in ipaddress.ip_network('fd7a:115c:a1e0::/48')):
            return 'Tailscale / 共享地址段' if ip.version == 4 else 'Tailscale 内网'
        if ip.is_link_local:
            return '链路本地地址'
        if ip.is_multicast or ip.is_unspecified or ip.is_reserved:
            return '特殊用途地址'
        if ip.is_private:
            return '内网 / 非公网地址'
        if not ip.is_global:
            return '内网 / 非公网地址'
        if not self.enabled:
            return '属地查询已关闭'
        key = str(ip)
        with self.lock:
            cached = self.cache.get(key)
            if cached and cached[1] > time.monotonic():
                self.cache.move_to_end(key)
                return cached[0]
            if self.closed.is_set():
                return '属地查询已停止'
            if key not in self.pending:
                try:
                    self.jobs.put_nowait(key)
                except queue.Full:
                    return '属地查询繁忙，稍后重试'
                self.pending.add(key)
                if self.worker is None:
                    self.worker = threading.Thread(target=self._run, name='geoip-lookup', daemon=True)
                    self.worker.start()
            return '属地查询中…'

    def _fetch(self, ip):
        with httpx.Client(timeout=3, follow_redirects=False, headers={'User-Agent': 'HTTP-DISTRIBUTE/1.0'}) as client:
            with client.stream('GET', self.API_URL, params={'type': 'json', 'ip': ip}) as response:
                response.raise_for_status()
                body = bytearray()
                for chunk in response.iter_bytes():
                    body.extend(chunk)
                    if len(body) > 65536:
                        raise ValueError('oversized geolocation response')
        import json
        result = json.loads(body)
        if result.get('code') != 0:
            raise ValueError('geolocation API rejected request')
        location = result.get('data', {}).get('location')
        if not isinstance(location, str) or not location.strip():
            raise ValueError('missing geolocation')
        return location.strip()[:300]

    def _run(self):
        while not self.closed.is_set():
            try:
                key = self.jobs.get(timeout=.2)
            except queue.Empty:
                continue
            try:
                location, ttl = self._fetch(key), 86400
            except Exception:
                location, ttl = '属地查询失败（稍后重试）', 60
            with self.lock:
                self.cache[key] = (location, time.monotonic() + ttl)
                self.cache.move_to_end(key)
                self.pending.discard(key)
                while len(self.cache) > 4096:
                    self.cache.popitem(last=False)
            self.jobs.task_done()
            # Bound upstream request frequency, including distinct historical IPs.
            self.closed.wait(1)

    def close(self):
        self.closed.set()
        if self.worker:
            self.worker.join(timeout=4)
