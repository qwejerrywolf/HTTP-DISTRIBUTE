import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest
from fastapi.testclient import TestClient
from config import Config
from app.context import Context
from app.admin import create_admin
from app.download import create_download


@pytest.fixture
def env(tmp_path):
    ctx = Context(Config(data_dir=str(tmp_path / 'data')))
    root = tmp_path / 'files'
    root.mkdir()
    file = root / '中文 空格 # %.bin'
    file.write_bytes(bytes(range(256))*4096)
    ctx.auth.initialize('admin', 'test-password-only-123')
    admin = TestClient(create_admin(ctx))
    admin.headers['x-httpd-request'] = '1'
    r = admin.post('/api/login', json={'username':'admin','password':'test-password-only-123'})
    admin.headers['x-csrf-token'] = r.json()['csrf']
    download = TestClient(create_download(ctx))
    yield ctx, admin, download, root, file
    admin.close()
    download.close()
    ctx.close()


def link(admin, path, **kwargs):
    r = admin.post('/api/links', json={'path':str(path), **kwargs})
    assert r.status_code == 200, r.text
    return r.json()
