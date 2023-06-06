from unittest.mock import patch

import pytest
from charm import TraefikIngressCharm
from scenario import Context


@pytest.fixture
def traefik_charm():
    with patch("charm.KubernetesServicePatch"), patch("lightkube.core.client.GenericSyncClient"):
        yield TraefikIngressCharm


@pytest.fixture
def traefik_ctx(traefik_charm):
    return Context(charm_type=traefik_charm)
