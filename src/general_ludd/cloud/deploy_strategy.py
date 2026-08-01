"""Cloud deploy strategy — gateway factory for Azure GPU inference endpoints."""

from __future__ import annotations

import os
from typing import Any


def build_azure_gateway() -> Any:
    """Construct a real ModelGateway backed by Azure GPU if available.

    Returns None when no Azure endpoint is configured.  Never returns a fake.
    """
    base_url = os.environ.get("AZURE_BASE_URL", "")
    if not base_url:
        return None

    model = os.environ.get("AZURE_MODEL", "qwen2.5-coder-7b")

    try:
        from general_ludd.models.gateway import ModelGateway, ModelProfile
    except ImportError:
        return None

    profile = ModelProfile(
        model_profile_id="azure_self_improve",
        provider="openai",
        model_name=model,
        api_base_alias="AZURE_BASE_URL",
        credential_alias="AZURE_API_KEY",
        enabled=True,
        api_metered=False,
    )
    return ModelGateway(profiles=[profile])
