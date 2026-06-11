from __future__ import annotations

from general_ludd.db import __all__ as db_all
from general_ludd.db import stamp_head


def test_stamp_head_in_all():
    assert "stamp_head" in db_all


def test_stamp_head_imports():
    assert callable(stamp_head)
