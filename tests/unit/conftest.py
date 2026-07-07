"""Ensure yaml CSafeLoader is stripped before any test module triggers an ansible import.

Python 3.14: the compiled yaml C extension (CSafeLoader/CParser) raises
    yaml.constructor.ConstructorError: could not determine a constructor for the tag None
when parsing ansible/config/base.yml.  We strip CSafeLoader from the yaml
module BEFORE ansible loads, forcing ansible to fall back to the pure-Python
SafeLoader via its existing except (ImportError, AttributeError) catch in
ansible/module_utils/common/yaml.py:39-43.
"""

import yaml as _yaml_mod

for _name in ("CSafeLoader", "CSafeDumper", "CParser"):
    _yaml_mod.__dict__.pop(_name, None)
del _yaml_mod, _name
