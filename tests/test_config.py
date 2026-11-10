import pytest
from config import Config


@pytest.mark.parametrize('host',['0.0.0.0','::','192.168.1.1','8.8.8.8'])
def test_public_management_binding_rejected(host):
    with pytest.raises(ValueError):Config(admin_host=host).validate()


@pytest.mark.parametrize('host',['127.0.0.1','::1','100.80.90.100','fd7a:115c:a1e0::1234'])
def test_private_management_binding_accepted(host):
    assert Config(admin_host=host).validate().admin_host==host


@pytest.mark.parametrize('host',['0.0.0.0','::','192.168.1.1','8.8.8.8'])
def test_explicit_public_management_binding_accepted(host):
    assert Config(admin_host=host,allow_public_admin=True).validate().admin_host==host


@pytest.mark.parametrize('value',['false','true',0,1,None])
def test_public_admin_flag_requires_boolean(value):
    with pytest.raises(ValueError,match='JSON 布尔值'):
        Config(admin_host='0.0.0.0',allow_public_admin=value).validate()


def test_public_admin_config_loads_from_json(tmp_path,monkeypatch):
    from config import load_config
    path=tmp_path/'config.json'
    path.write_text('{"admin_host":"0.0.0.0","allow_public_admin":true}')
    monkeypatch.setenv('HTTPD_CONFIG',str(path))
    config=load_config()
    assert config.allow_public_admin is True and config.admin_host=='0.0.0.0'
