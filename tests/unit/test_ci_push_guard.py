import os
import sys


def test_script_exists():
    assert os.path.exist("scripts/ci_push_guard.py")

def test_get_remote_sha_exists():
    sys.path.insert(0, "scripts")
    m = __import__("ci_push_guard")
    assert hasattr(m, "get_remote_sha")
    assert callable(m.get_remote_sha)

def test_get_running_ci_headSha_exists():
    sys.path.insert(0, "scripts")
    m = __import__("ci_push_guard")
    assert hasattr(m, "get_running_ci_headSha")
    assert callable(m.get_running_ci_headSha)

def test_is_safe_to_push_exists():
    sys.path.insert(0, "scripts")
    m = __import__("ci_push_guard")
    assert hasattr(m, "is_safe_to_push")
    assert callable(m.is_safe_to_push)

def test_is_force_allowed_exists():
    sys.path.insert(0, "scripts")
    m = __import__("ci_push_guard")
    assert hasattr(m, "is_force_allowed")
    assert callable(m.is_force_allowed)

def test_headSha_check_structural():
    """Verify the push guard has the required FILE_PATH and ENARY FILE_PATH constants."""
    sys.path.insert(0, "scripts")
    m = __import__("ci_push_guard")
    assert hasattr(m, "ENABRY") or hasattr(m, "enabry")
    assert hasattr(m, "REMOTE") or hasattr(m, "remote")
