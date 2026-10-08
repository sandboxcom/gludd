#!/usr/bin/python
"""Verify one bounded release artifact and its dependency lock locally."""

from __future__ import annotations

from typing import Any

from ansible.module_utils.basic import AnsibleModule
from ansible_collections.general_ludd.git_release.plugins.module_utils.provenance import (
    ArtifactVerificationError,
    verify_release_artifact,
)

DOCUMENTATION = r"""
---
module: git_release
short_description: Verify a release artifact and dependency lock by exact SHA-256
description:
  - Opens one regular artifact and dependency lock below an explicit root.
  - Rejects symbolic links, path escapes, oversize files, mutation during reads,
    and every checksum mismatch.
options:
  root:
    description: Absolute no-symlink root containing both inputs.
    type: path
    required: true
  artifact_path:
    description: Relative path to the release artifact.
    type: str
    required: true
  artifact_sha256:
    description: Exact lowercase SHA-256 of the release artifact.
    type: str
    required: true
  lock_path:
    description: Relative path to the dependency lock.
    type: str
    required: true
  lock_sha256:
    description: Exact lowercase SHA-256 of the dependency lock.
    type: str
    required: true
author:
  - Gludd
"""

EXAMPLES = r"""
- name: Verify the immutable release input pair
  general_ludd.git_release.git_release:
    root: /srv/gludd/releases/v0.1.2
    artifact_path: dist/general_ludd-0.1.2.whl
    artifact_sha256: "0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef"
    lock_path: uv.lock
    lock_sha256: "abcdef0123456789abcdef0123456789abcdef0123456789abcdef0123456789"
"""

RETURN = r"""
result:
  description: Exact read-only digest and size evidence.
  returned: success
  type: dict
"""


def run(module: Any) -> None:
    """Run identical read-only verification in normal and check mode."""
    try:
        result = verify_release_artifact(
            root=module.params["root"],
            artifact_path=module.params["artifact_path"],
            artifact_sha256=module.params["artifact_sha256"],
            lock_path=module.params["lock_path"],
            lock_sha256=module.params["lock_sha256"],
        )
    except (ArtifactVerificationError, OSError) as exc:
        module.fail_json(changed=False, msg=str(exc))
        return
    module.exit_json(changed=False, result=result)


def main() -> None:
    """Build the narrow argument contract and verify local evidence."""
    module = AnsibleModule(
        argument_spec={
            "root": {"type": "path", "required": True},
            "artifact_path": {"type": "str", "required": True},
            "artifact_sha256": {"type": "str", "required": True},
            "lock_path": {"type": "str", "required": True},
            "lock_sha256": {"type": "str", "required": True},
        },
        supports_check_mode=True,
    )
    run(module)


if __name__ == "__main__":
    main()
