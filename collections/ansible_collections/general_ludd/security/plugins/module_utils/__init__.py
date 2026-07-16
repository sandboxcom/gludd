"""
security collection module_utils -- security analysis and cross-collection references.

Cross-collection references:
    general_ludd.binary_re.plugins.module_utils.prompt_injection_detector
        - Regex/AST-based prompt-injection payload detection shared with security roles

    general_ludd.binary_re.plugins.module_utils.fuzzing_strategies
        - AFL++/libFuzzer strategies for security fuzzing campaigns

    general_ludd.binary_re.plugins.module_utils.obfuscation_techniques
        - Obfuscation technique detection for security audit of obfuscated payloads
"""

from __future__ import annotations

try:
    from ansible_collections.general_ludd.binary_re.plugins.module_utils.prompt_injection_detector import (  # noqa: F401
        scan_for_injection,
    )
    _HAS_BINARY_RE_INJECTION = True
except ImportError:
    _HAS_BINARY_RE_INJECTION = False

try:
    from ansible_collections.general_ludd.binary_re.plugins.module_utils.fuzzing_strategies import (  # noqa: F401
        FuzzingStrategy,
    )
    _HAS_BINARY_RE_FUZZING = True
except ImportError:
    _HAS_BINARY_RE_FUZZING = False


def binary_re_modules_available() -> dict[str, bool]:
    """Return which binary_re modules are importable."""
    return {
        "prompt_injection_detector": _HAS_BINARY_RE_INJECTION,
        "fuzzing_strategies": _HAS_BINARY_RE_FUZZING,
    }


__all__ = [
    "binary_re_modules_available",
]
