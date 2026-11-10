import pytest
from config import Config


@pytest.mark.parametrize('host',['0.0.0.0','::','192.168.1.1','8.8.8.8'])
def test_public_management_binding_rejected(host):
    with pytest.raises(ValueError):Config(admin_host=host).validate()


@pytest.mark.parametrize('host',['127.0.0.1','::1','100.80.90.100','fd7a:115c:a1e0::1234'])
def test_private_management_binding_accepted(host):
    assert Config(admin_host=host).validate().admin_host==host
