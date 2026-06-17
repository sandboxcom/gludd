"""Secrets module."""

from general_ludd.secrets.config import OpenBaoConfig
from general_ludd.secrets.env import EnvSecretsManager
from general_ludd.secrets.manager import (
    AppRoleCreds,
    BootstrapResult,
    ImageUpdateCandidate,
    SecretAlias,
    SecretsManager,
    SecretsUnavailableError,
)
from general_ludd.secrets.protocol import SecretsManagerProtocol

__all__ = [
    "AppRoleCreds",
    "BootstrapResult",
    "EnvSecretsManager",
    "ImageUpdateCandidate",
    "OpenBaoConfig",
    "SecretAlias",
    "SecretsManager",
    "SecretsManagerProtocol",
    "SecretsUnavailableError",
]
