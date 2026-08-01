import os
import sys


def test_script_exists():
    assert os.path.exists("scripts/check_ci_integrity.py")

    sys.path.insert(0, "scripts")
    m = __import__("check_ci_integrity")
    assert hasattr(m, "CI_CRITICAL")
    assert len(m.CI_CRITICAL) >= 1

    assert "require_ci_green.py" in str(m.CI_CRITICAL)

    assert hasattr(m, "main")
    assert callable(m.main)
