"""Ensure yaml CSafeLoader is stripped before any test module triggers an ansible import.

Python 3.14: the compiled yaml C extension (CSafeLoader/CParser) raises
    yaml.constructor.ConstructorError: could not determine a constructor for the tag None
when parsing ansible/config/base.yml.  We strip CSafeLoader from the yaml
module BEFORE ansible loads, forcing ansible to fall back to the pure-Python
SafeLoader via its existing except (ImportError, AttributeError) catch in
ansible/module_utils/common/yaml.py:39-43.

Also sets up the ansible collections path so travel test modules can import
ansible_collections at module level without sys.path hacks.
"""

import sys
from pathlib import Path

import yaml as _yaml_mod

for _name in ("CSafeLoader", "CSafeDumper", "CParser"):
    _yaml_mod.__dict__.pop(_name, None)

_collections = Path(__file__).resolve().parent.parent.parent / "collections"
if str(_collections) not in sys.path:
    sys.path.insert(0, str(_collections))

del _yaml_mod, _name, _collections
