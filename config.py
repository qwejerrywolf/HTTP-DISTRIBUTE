import json
import os
import ipaddress
from dataclasses import dataclass, fields
from pathlib import Path

ROOT = Path(__file__).resolve().parent


@dataclass
class Config:
    download_host: str = '0.0.0.0'
    download_port: int = 18080
    admin_host: str = '127.0.0.1'
    admin_port: int = 18081
    allow_public_admin: bool = False
    public_url: str = 'http://localhost:18080'
    cookie_secure: bool = False
    data_dir: str = 'data'
    max_downloads: int = 64
    session_hours: int = 12
    history_days: int = 90
    geoip_enabled: bool = True
    geoip_database: str = ''  # Accepted for compatibility; no longer used.

    def validate(self):
        if type(self.geoip_enabled) is not bool:
            raise ValueError('geoip_enabled 必须为 JSON 布尔值 true 或 false')
        if type(self.allow_public_admin) is not bool:
            raise ValueError('allow_public_admin 必须为 JSON 布尔值 true 或 false')
        ip = ipaddress.ip_address(self.admin_host)
        tailscale = ip in ipaddress.ip_network('100.64.0.0/10') if ip.version == 4 else ip in ipaddress.ip_network('fd7a:115c:a1e0::/48')
        if not self.allow_public_admin and not (ip.is_loopback or tailscale):
            raise ValueError('管理服务默认仅允许回环地址或 Tailscale IP；如需其他监听地址，请设置 allow_public_admin=true')
        if not all(1 <= p <= 65535 for p in (self.download_port, self.admin_port)):
            raise ValueError('端口必须为 1..65535')
        if self.download_port == self.admin_port:
            raise ValueError('两个服务必须使用不同端口')
        if not 1 <= self.max_downloads <= 512 or not 1 <= self.session_hours <= 168 or self.history_days < 1:
            raise ValueError('并发数、Session 时长或历史保留天数无效')
        self.data_dir = str(Path(self.data_dir).absolute())
        return self


def load_config():
    path = Path(os.environ.get('HTTPD_CONFIG', ROOT / 'config.json'))
    raw = json.loads(path.read_text('utf-8')) if path.exists() else {}
    unknown = set(raw) - {f.name for f in fields(Config)}
    if unknown:
        raise ValueError(f'未知配置项: {unknown}')
    if os.environ.get('HTTPD_DATA'):
        raw['data_dir'] = os.environ['HTTPD_DATA']
    return Config(**raw).validate()
