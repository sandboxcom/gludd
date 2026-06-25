#!/usr/bin/python
# -*- coding: utf-8 -*-
# Copyright: Agentic Harness
# SPDX-License-Identifier: MIT
"""
DOCUMENTATION:
  module: gludd_embed
  short_description: Find the canonical task types most similar to a work description
  description:
    - Queries the daemon's read-only C(POST /api/embeddings/similar) endpoint and
      returns the ranked similar canonical task types under
      C(ansible_facts.gludd_embed) so a playbook (or the model running a job) can
      borrow a good model/prompt from a semantically-neighboring task type.
    - Read-only and check-mode safe — it performs no writes (C(changed=False)).
    - v1 covers the 10 canonical task types only. The similarity is computed over
      the same embedding layer the adaptive router uses (HashEmbedder offline,
      OpenAIEmbedder when C(OPENAI_API_KEY) is set on the daemon).
  options:
    text:
      description: The work description to match against the canonical task types.
      type: str
      required: true
    top_k:
      description: Number of similar task types to return (1-20).
      type: int
      default: 5
    work_type:
      description:
        - Optional filter — restrict the result to this canonical task-type value
          (e.g. C(bug_fix), C(feature), C(refactor)).
      type: str
    include_embedding:
      description: When true, the query embedding vector is returned in the snapshot.
      type: bool
      default: false
    daemon_url:
      description: Base URL of the daemon.
      type: str
      default: "http://localhost:8000"
    psk:
      description: Pre-shared key for daemon auth.
      type: str
      no_log: true
      default: ""
    timeout:
      description: Request timeout in seconds.
      type: int
      default: 30

EXAMPLES:
  - name: Find task types similar to this work
    general_ludd.agent.gludd_embed:
      text: "Track down why the request handler intermittently returns 500s"
    register: embed

  - name: Use the closest task type's routing
    ansible.builtin.debug:
      msg: >-
        Closest type is
        {{ ansible_facts.gludd_embed.results[0].task_type }}
        ({{ ansible_facts.gludd_embed.results[0].similarity_score }})
    when: ansible_facts.gludd_embed.results | length > 0

RETURN:
  ansible_facts:
    description: Facts dict containing the C(gludd_embed) snapshot.
    type: dict
    returned: always
    contains:
      gludd_embed:
        description:
          - The similarity snapshot with C(results) (ranked task types),
            C(query_embedding_dim), C(embedding_method), and optionally
            C(query_embedding).
        type: dict
        returned: always
"""

from __future__ import annotations

import os

from ansible.module_utils.basic import AnsibleModule  # type: ignore[import]

try:
    from ansible_collections.general_ludd.agent.plugins.module_utils.gludd import (
        GluddClient,
        error_result,
        ok_result,
    )
except ImportError:
    import sys
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "module_utils"))
    from gludd import GluddClient, error_result, ok_result  # type: ignore[import]


def main() -> None:
    module = AnsibleModule(
        argument_spec=dict(
            text=dict(type="str", required=True),
            top_k=dict(type="int", default=5),
            work_type=dict(type="str"),
            include_embedding=dict(type="bool", default=False),
            daemon_url=dict(type="str", default="http://localhost:8000"),
            psk=dict(type="str", default="", no_log=True),
            timeout=dict(type="int", default=30),
        ),
        supports_check_mode=True,
    )

    client = GluddClient(
        base_url=module.params["daemon_url"],
        psk=module.params["psk"],
        timeout=module.params["timeout"],
    )

    body: dict[str, object] = {
        "text": module.params["text"],
        "top_k": module.params["top_k"],
        "include_embedding": module.params["include_embedding"],
    }
    if module.params.get("work_type"):
        body["work_type"] = module.params["work_type"]

    resp = client.post("/api/embeddings/similar", body=body)
    if resp.get("_error"):
        module.fail_json(**error_result(f"daemon error: {resp['_error']}"))
        return
    status_code = resp.get("_status", 0)
    if status_code == 401:
        module.fail_json(**error_result("unauthorized (bad or missing PSK)", status=401))
        return
    if status_code not in (200, 201):
        module.fail_json(
            **error_result(
                f"gludd_embed failed (HTTP {status_code})", status=status_code
            )
        )
        return

    snapshot = {k: v for k, v in resp.items() if not k.startswith("_")}
    module.exit_json(
        **ok_result({"ansible_facts": {"gludd_embed": snapshot}}, changed=False)
    )


if __name__ == "__main__":
    main()
