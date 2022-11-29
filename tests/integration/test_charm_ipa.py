# Copyright 2022 Canonical Ltd.
# See LICENSE file for licensing details.
import pytest
import yaml
from pytest_operator.plugin import OpsTest

from tests.integration.conftest import (
    assert_can_ping,
    deploy_traefik_if_not_deployed,
    get_address,
    get_relation_data,
)


@pytest.mark.abort_on_fail
async def test_deployment(ops_test: OpsTest, traefik_charm, ipa_tester_charm):
    await deploy_traefik_if_not_deployed(ops_test, traefik_charm)
    await ops_test.model.deploy(ipa_tester_charm, "ipa-tester", series="focal")
    async with ops_test.fast_forward():
        await ops_test.model.wait_for_idle(["traefik-k8s", "ipa-tester"], status="active")


@pytest.mark.abort_on_fail
async def test_relate(ops_test: OpsTest):
    await ops_test.model.add_relation("ipa-tester:ingress", "traefik-k8s:ingress")
    async with ops_test.fast_forward():
        await ops_test.model.wait_for_idle(["traefik-k8s", "ipa-tester"])


def assert_ipa_charm_has_ingress(ops_test: OpsTest):
    data = get_relation_data(
        requirer_endpoint="ipa-tester/0:ingress",
        provider_endpoint="traefik-k8s/0:ingress",
        model=ops_test.model_full_name,
    )
    provider_app_data = yaml.safe_load(data.provider.application_data["ingress"])
    url = provider_app_data["url"]
    ip, port = url.split("//")[1].split("/")[0].split(":")
    assert_can_ping(ip, port)


async def test_ipa_charm_has_ingress(ops_test: OpsTest):
    assert_ipa_charm_has_ingress(ops_test)


@pytest.mark.abort_on_fail
async def test_relation_data_shape(ops_test: OpsTest):
    data = get_relation_data(
        requirer_endpoint="ipa-tester/0:ingress",
        provider_endpoint="traefik-k8s/0:ingress",
        model=ops_test.model_full_name,
    )

    requirer_app_data = data.requirer.application_data
    # example:
    # host: foo.bar
    # model: foo
    # name: ipa-tester/0
    # port: 8080
    model = requirer_app_data["model"]
    assert requirer_app_data["host"] == "foo.bar"
    assert requirer_app_data["name"] == "ipa-tester"
    assert requirer_app_data["port"] == "80"

    # example:
    #  ingress:
    #    url: http://foo.bar:80/foo-ipa-tester/0
    traefik_address = await get_address(ops_test, "traefik-k8s")
    provider_app_data = yaml.safe_load(data.provider.application_data["ingress"])
    assert provider_app_data == {"url": f"http://{traefik_address}:80/{model}-ipa-tester"}


async def test_remove_relation(ops_test: OpsTest):
    await ops_test.juju("remove-relation", "ipa-tester:ingress", "traefik-k8s:ingress")
    async with ops_test.fast_forward():
        await ops_test.model.wait_for_idle(["traefik-k8s", "ipa-tester"], status="active")
