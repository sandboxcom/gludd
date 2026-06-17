"""Regression: is_path_within renamed to is_join_within with a back-compat alias."""
import tempfile

from general_ludd.security import is_join_within as exported
from general_ludd.security.auth import is_join_within, is_path_within


def test_alias_is_same_object():
    assert is_path_within is is_join_within


def test_join_within_confines():
    with tempfile.TemporaryDirectory() as d:
        assert is_join_within(d, "sub.txt") is True
        assert is_join_within(d, "../escape") is False


def test_exported_from_package():
    assert exported is is_join_within
