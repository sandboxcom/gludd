"""General Ludd Agent - autonomous coding system with Ansible runners and multi-model AI agents."""

from __future__ import annotations

import importlib
import sys
import warnings

# Python 3.13+: pkg_resources was removed from the stdlib.
# The ``fs`` (pyfilesystem) package calls ``__import__("pkg_resources").declare_namespace``
# at module load time, which fails with ModuleNotFoundError.
# Provide a stub ``pkg_resources`` module that exposes a no-op
# ``declare_namespace`` so ``fs`` can import without error.
_FAKE_PKG_RESOURCES = type(sys)("pkg_resources")
object.__setattr__(_FAKE_PKG_RESOURCES, "declare_namespace", lambda _name: None)
sys.modules["pkg_resources"] = _FAKE_PKG_RESOURCES
del _FAKE_PKG_RESOURCES

_annotated_patch_mod = importlib.import_module("general_ludd.compat.annotated_types")

_annotated_patch_mod.apply_annotated_types_runtime_patch()

warnings.filterwarnings(
    "ignore",
    message="pkg_resources is deprecated as an API.*",
    category=UserWarning,
    module="fs",
)

__version__ = "0.1.0-beta.4"
