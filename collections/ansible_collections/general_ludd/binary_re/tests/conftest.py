"""Test configuration for binary_re collection tests."""

import os
import sys

_collection_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _collection_root not in sys.path:
    sys.path.insert(0, _collection_root)

_collections_root = os.path.abspath(os.path.join(_collection_root, "..", "..", ".."))
if _collections_root not in sys.path:
    sys.path.insert(0, _collections_root)
