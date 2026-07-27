"""Test configuration for governance collection tests."""

import os
import sys

_collection_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _collection_root not in sys.path:
    sys.path.insert(0, _collection_root)
