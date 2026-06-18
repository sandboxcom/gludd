"""E2E scaffold: Azure full provision/teardown (opt-in, costly, manual/scheduled).

THIS TEST PROVISIONS REAL AZURE INFRASTRUCTURE (GPU VMs) AND INCURS COST.
DO NOT RUN IN CI / PER-PR GATES.

It is gated behind AZURE_PROVISION_E2E=1 and skips unconditionally otherwise.
Intended for nightly/weekly scheduled runs.

Required env vars:
  AZURE_PROVISION_E2E=1             hard opt-in gate
  AZURE_SUBSCRIPTION_ID             Azure subscription
  AZURE_RESOURCE_GROUP              resource group (must exist)
  GLUDD_E2E_MAX_SPEND_USD           spend ceiling — test refuses to proceed if
                                    estimated cost exceeds this
  AZURE_PROVISION_ENGINE            one of: vllm, ollama, llamacpp, slurm
                                    (default: vllm)
  AZURE_PROVISION_MODEL             model to serve (default: Qwen/Qwen2.5-0.5B)

Azure auth: standard azure-identity credential chain (service principal via
AZURE_CLIENT_ID / AZURE_CLIENT_SECRET / AZURE_TENANT_ID, or az login).

Wave-C TODO: implement the full provision → serve → call → bill → destroy
spine using TerraformGenerator + DeploymentManager (see DESIGN §4.6).
The try/finally guaranteed teardown is MANDATORY — never leave a leaked VM.
"""

from __future__ import annotations

import os

import pytest

from tests.e2e.providers._provider_skip import require_azure_provision

pytestmark = [pytest.mark.e2e, pytest.mark.slow, pytest.mark.azure_provision]

_PROVISION_ENGINE_DEFAULT = "vllm"
_PROVISION_MODEL_DEFAULT = "Qwen/Qwen2.5-0.5B-Instruct"


def _provision_engine() -> str:
    return os.environ.get("AZURE_PROVISION_ENGINE", _PROVISION_ENGINE_DEFAULT)


def _provision_model() -> str:
    return os.environ.get("AZURE_PROVISION_MODEL", _PROVISION_MODEL_DEFAULT)


def _max_spend_usd() -> float:
    raw = os.environ.get("GLUDD_E2E_MAX_SPEND_USD", "")
    try:
        return float(raw)
    except (ValueError, TypeError):
        pytest.skip(
            "GLUDD_E2E_MAX_SPEND_USD not set or not a number — "
            "required to enforce a cost ceiling on the provision test"
        )
        return 0.0  # unreachable; pytest.skip raises


# ---------------------------------------------------------------------------
# Test: full provision → serve → discover → call → bill → destroy
# ---------------------------------------------------------------------------

class TestAzureProvision:
    """Full provision/teardown test using TerraformGenerator + DeploymentManager.

    IMPORTANT: the try/finally teardown below MUST always run. Do not add any
    code path that can skip the finally block. A leaked Azure VM costs real money.

    TODO(Wave-C): implement the full spine:
      1. TerraformGenerator (infra/terraform.py) renders Azure GPU instance IaC
         with cloud-init to install + serve the chosen engine.
      2. DeploymentManager (infra/deployment.py) provisions it, yields
         ComputeInstance(provider=ComputeProvider.AZURE, endpoint_url=...).
      3. try/finally: finally always calls DeploymentManager.destroy().
      4. Poll endpoint_url/v1/models until ready (bounded, skip on timeout).
      5. Run model call + register + bill assertions from test_azure_e2e.py.
      6. Assert cost_incurred < GLUDD_E2E_MAX_SPEND_USD.
      7. For engine=slurm, run SlurmAdapter submit/poll/cancel assertions.
    """

    def test_provision_full_spine(self) -> None:
        """Provision an Azure GPU VM, serve a model, call it, bill it, destroy it."""
        # Hard opt-in gate: raises pytest.skip if not set
        require_azure_provision()

        engine = _provision_engine()
        model = _provision_model()
        max_spend = _max_spend_usd()

        assert max_spend > 0, "GLUDD_E2E_MAX_SPEND_USD must be > 0"

        # TODO(Wave-C): import and use TerraformGenerator + DeploymentManager
        try:
            from general_ludd.infra.deployment import DeploymentManager  # noqa: F401
            from general_ludd.infra.terraform import TerraformGenerator  # noqa: F401
        except ImportError:
            pytest.skip(
                "TerraformGenerator or DeploymentManager not importable — "
                "provision test requires the full infra module"
            )

        # TODO(Wave-C): implement the provision lifecycle with guaranteed teardown.
        # The skeleton below shows the required structure; fill it in when
        # this test is promoted from scaffold to real.
        #
        # instance = None
        # try:
        #     generator = TerraformGenerator()
        #     iac = generator.generate_azure_gpu_instance(
        #         resource_group=os.environ["AZURE_RESOURCE_GROUP"],
        #         subscription_id=os.environ["AZURE_SUBSCRIPTION_ID"],
        #         engine=engine,
        #         model=model,
        #     )
        #     manager = DeploymentManager()
        #     instance = manager.provision(iac, timeout_seconds=600)
        #     assert instance.endpoint_url, "No endpoint_url after provisioning"
        #     assert instance.cost_incurred < max_spend, (
        #         f"cost_incurred {instance.cost_incurred} exceeds ceiling {max_spend}"
        #     )
        #     # Run azure env-pointer assertions against the freshly-provisioned endpoint
        #     # (reuse test_azure_e2e.py assertions here)
        # finally:
        #     if instance is not None:
        #         manager.destroy(instance)
        #         assert instance.status == "destroyed"  # no leaked VM

        pytest.skip(
            "Azure provision test not yet implemented (Wave-C). "
            f"Would have provisioned engine={engine!r}, model={model!r}, "
            f"max_spend=${max_spend:.2f}."
        )

    def test_provision_spend_ceiling_enforced(self) -> None:
        """Assert the spend ceiling check fires before provisioning begins.

        TODO(Wave-C): when the full spine is implemented, this test verifies
        that pre-flight cost estimation refuses to proceed when the estimated
        GPU VM cost exceeds GLUDD_E2E_MAX_SPEND_USD=0.01 (a token value).
        """
        require_azure_provision()
        pytest.skip(
            "Spend ceiling enforcement test not yet implemented (Wave-C). "
            "Will assert that estimated_cost > GLUDD_E2E_MAX_SPEND_USD raises "
            "before any terraform apply."
        )

    def test_slurm_subvariant_on_azure(self) -> None:
        """After provisioning an Azure VM with Slurm, run the submit/poll/cancel loop.

        TODO(Wave-C): when the full spine is implemented, add engine=slurm
        variant: provision an HPC cluster, then reuse test_slurm_e2e.py
        assertions via SlurmAdapter(api_url=<provisioned controller REST>).
        """
        require_azure_provision()
        if _provision_engine() != "slurm":
            pytest.skip(
                "AZURE_PROVISION_ENGINE != 'slurm' — this sub-variant only "
                "runs when the provisioned VM is a Slurm controller"
            )
        pytest.skip("Azure+Slurm sub-variant not yet implemented (Wave-C)")
