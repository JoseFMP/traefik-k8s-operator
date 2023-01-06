#!/usr/bin/env python3
# Copyright 2022 Canonical Ltd.
# See LICENSE file for licensing details.

"""This module tests that traefik ends up in active state when deployed AFTER metallb.

...And without the help of update-status.

1. Enable metallb (in case it's disabled).
2. Deploy traefik + one charm per relation type (as if deployed as part of a bundle).

NOTE: This module implicitly relies on in-order execution (test running in the order they are
 written).
"""

import asyncio
import json
import logging
import tempfile
from pathlib import Path
from types import SimpleNamespace
from urllib.request import urlopen

import pytest
import yaml
from pytest_operator.plugin import OpsTest

from tests.integration.helpers import disable_metallb, enable_metallb, get_address

logger = logging.getLogger(__name__)

idle_period = 90

METADATA = yaml.safe_load(Path("./metadata.yaml").read_text())
resources = {"traefik-image": METADATA["resources"]["traefik-image"]["upstream-source"]}
trfk = SimpleNamespace(name="traefik", resources=resources)
mock_hostname = "juju.local"  # For TLS

ipu = SimpleNamespace(charm="ch:prometheus-k8s", name="prometheus")  # per unit
ipa = SimpleNamespace(charm="ch:alertmanager-k8s", name="alertmanager")  # per app
ipr = SimpleNamespace(charm="ch:grafana-k8s", name="grafana")  # traefik route


def get_endpoints(ops_test: OpsTest, *, scheme: str, netloc: str) -> list:
    """Return a list of all the URLs that are expected to be reachable (HTTP code < 400)."""
    return [
        f"{scheme}://{netloc}/{path}"
        for path in [
            f"{ops_test.model_name}-{ipu.name}-0",
            f"{ops_test.model_name}-{ipa.name}",
            f"{ops_test.model_name}-{ipr.name}",
        ]
    ]


@pytest.mark.abort_on_fail
async def test_build_and_deploy(ops_test: OpsTest, traefik_charm):
    # logger.info("First, disable metallb, in case it's enabled")
    # await disable_metallb()
    # logger.info("Now enable metallb")
    # await enable_metallb()

    await asyncio.gather(
        ops_test.model.deploy(
            traefik_charm, resources=trfk.resources, application_name=trfk.name, series="focal"
        ),
        ops_test.model.deploy(
            ipu.charm,
            application_name=ipu.name,
            channel="edge",  # TODO change to "stable" once available
            trust=True,
            series="focal",
        ),
        ops_test.model.deploy(
            ipa.charm,
            application_name=ipa.name,
            channel="edge",  # TODO change to "stable" once available
            trust=True,
            series="focal",
        ),
        ops_test.model.deploy(
            ipr.charm,
            application_name=ipr.name,
            channel="edge",  # TODO change to "stable" once available
            trust=True,
            series="focal",
        ),
    )

    await ops_test.model.wait_for_idle(
        status="active", timeout=600, idle_period=30, raise_on_error=False
    )

    await asyncio.gather(
        ops_test.model.add_relation(f"{ipu.name}:ingress", trfk.name),
        ops_test.model.add_relation(f"{ipa.name}:ingress", trfk.name),
        ops_test.model.add_relation(f"{ipr.name}:ingress", trfk.name),
    )

    await ops_test.model.wait_for_idle(status="active", timeout=600, idle_period=30)


@pytest.mark.abort_on_fail
async def test_ingressed_endpoints_reachable_after_metallb_enabled(ops_test: OpsTest):
    ip = await get_address(ops_test, trfk.name)
    for ep in get_endpoints(ops_test, scheme="http", netloc=ip):
        logger.debug("Attempting to reach %s", ep)  # Traceback doesn't spell out the endpoint
        urlopen(ep)
        # A 404 would result in an exception:
        #   urllib.error.HTTPError: HTTP Error 404: Not Found
        # so just `urlopen` on its own should suffice for the test.


@pytest.mark.abort_on_fail
async def test_tls_termination(ops_test: OpsTest):
    # TODO move this to the bundle tests
    await ops_test.model.applications[trfk.name].set_config({"external_hostname": mock_hostname})

    await ops_test.model.deploy(
        "ch:tls-certificates-operator",
        application_name="root-ca",
        channel="edge",
    )
    await ops_test.model.applications["root-ca"].set_config(
        {
            "ca-common-name": "demo.ca.local",
            "generate-self-signed-certificates": "true",
        }
    )
    await ops_test.model.add_relation("root-ca", f"{trfk.name}:certificates")
    await ops_test.model.wait_for_idle(status="active", timeout=300)

    # Get self-signed cert from peer app data
    rc, stdout, stderr = await ops_test.run("juju", "show-unit", "root-ca/0", "--format=json")
    data = json.loads(stdout)
    peer_data = next(
        filter(lambda d: d["endpoint"] == "replicas", data["root-ca/0"]["relation-info"])
    )
    cert = peer_data["application-data"]["self_signed_ca_certificate"]

    # # Temporary workaround for grafana giving 404 (TODO remove when fixed)
    # num_rels_before = len(ops_test.model.applications[trfk.name].relations)
    # await ops_test.model.applications[trfk.name].remove_relation(f"{ipr.name}:ingress", trfk.name)
    # await ops_test.model.block_until(  # https://github.com/juju/python-libjuju/pull/665
    #     lambda: len(ops_test.model.applications[trfk.name].relations) < num_rels_before
    # )
    # await ops_test.model.applications[trfk.name].add_relation(f"{ipr.name}:ingress", trfk.name)

    with tempfile.TemporaryDirectory() as certs_dir:
        cert_path = f"{certs_dir}/local.cert"
        with open(cert_path, "wt") as f:
            f.writelines(cert)

        ip = await get_address(ops_test, trfk.name)
        for endpoint in get_endpoints(ops_test, scheme="https", netloc=mock_hostname):
            # Tell curl to resolve the mock_hostname as traefik's IP (to avoid using a custom DNS
            # server). This is needed because the certificate issued by the CA would have that same
            # hostname as the subject, and for TLS to succeed, the target url's hostname must match
            # the one in the certificate.
            rc, stdout, stderr = await ops_test.run(
                "curl",
                "-s",
                "--fail-with-body",
                "--resolve",
                f"{mock_hostname}:443:{ip}",
                "--capath",
                certs_dir,
                "--cacert",
                cert_path,
                endpoint,
            )
            logger.info("%s: %s", endpoint, (rc, stdout, stderr))
            assert rc == 0, (
                f"curl exited with rc={rc} for {endpoint}; "
                "non-zero return code means curl encountered a >= 400 HTTP code"
            )
