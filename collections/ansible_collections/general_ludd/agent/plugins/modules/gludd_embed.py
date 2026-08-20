#!/usr/bin/python
# Copyright: Agentic Harness
# SPDX-License-Identifier: MIT
"""
DOCUMENTATION:
  module: gludd_embed
  short_description: Embedding similarity over the daemon's bert surface
  description:
    - With C(op=similar) (the default) queries the daemon's read-only
      C(POST /api/embeddings/similar) endpoint and returns the ranked similar
      canonical task types under C(ansible_facts.gludd_embed) so a playbook (or
      the model running a job) can borrow a good model/prompt from a
      semantically-neighboring task type.
    - With C(op=compare) queries C(POST /api/embeddings/compare) to measure the
      pairwise similarity of two strings (C(text_a)/C(text_b)) produced by
      separate bots/agents — so a role can decide how to proceed (near-duplicate
      strings -> merge/dedupe; divergent -> escalate). Supply C(texts) (2+)
      instead for the full pairwise similarity matrix. The snapshot is injected
      under C(ansible_facts.gludd_embed).
    - With C(op=search) queries C(POST /api/embeddings/search) to take a string
      a bot produced (C(text)) and search a real corpus with it (RAG search),
      returning the C(top_k) most-similar items ranked by cosine similarity.
      C(corpus) selects the corpus — C(skills) (the live skill registry,
      descriptions matched on the fly), C(task_types) (the canonical task
      types), C(prompts) (the persisted prompt profiles), C(traces) (recent
      execution traces, work_type/phase/span descriptions matched on the fly),
      or C(events) (recent audit events, event_type/entity_type and a summary
      of the details JSON matched on the fly).
      The snapshot is injected under C(ansible_facts.gludd_embed).
    - Read-only and check-mode safe — it performs no writes (C(changed=False)).
    - Similarity is computed over the same embedding layer the adaptive router
      uses (HashEmbedder offline, OpenAIEmbedder when C(OPENAI_API_KEY) is set
      on the daemon).
  options:
    op:
      description:
        - Which embeddings operation to run. C(similar) ranks canonical task
          types for C(text); C(compare) measures pairwise similarity of
          C(text_a)/C(text_b) (or a C(texts) batch); C(search) takes C(text)
          and searches the C(corpus) for the C(top_k) most-similar items.
      type: str
      choices: [similar, compare, search]
      default: similar
    text:
      description:
        - The work description to match against the canonical task types
          (C(op=similar)) or the query string to search the corpus with
          (C(op=search)). Required for both.
      type: str
    corpus:
      description:
        - The real corpus to search when C(op=search). C(skills) matches the
          live skill registry; C(task_types) matches the canonical task types;
          C(prompts) matches the persisted prompt profiles (each prompt_text
          embedded on the fly); C(traces) matches recent execution traces (each
          trace's work_type/phase labels/span descriptions embedded on the fly);
          C(events) matches recent audit events (each event's
          event_type/entity_type and a summary of its details JSON embedded on
          the fly).
      type: str
      choices: [skills, task_types, prompts, traces, events]
      default: skills
    text_a:
      description: First string to compare. Used (with C(text_b)) when C(op=compare).
      type: str
    text_b:
      description: Second string to compare. Used (with C(text_a)) when C(op=compare).
      type: str
    texts:
      description:
        - Batch form for C(op=compare) — a list of 2+ strings; returns the
          pairwise similarity matrix instead of a single score.
      type: list
      elements: str
    include_embeddings:
      description:
        - When true, the computed embedding vectors are returned — the pairwise
          vectors for C(op=compare), or the query vector for C(op=search).
      type: bool
      default: false
    top_k:
      description:
        - Number of items to return (1-20) — similar task types for
          C(op=similar), or corpus matches for C(op=search).
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

  - name: Compare two strings from separate agents to decide merge vs escalate
    general_ludd.agent.gludd_embed:
      op: compare
      text_a: "{{ agent_one_output }}"
      text_b: "{{ agent_two_output }}"
    register: cmp

  - name: Treat near-duplicates as a merge
    ansible.builtin.debug:
      msg: "near-duplicate, dedupe"
    when: ansible_facts.gludd_embed.similarity | default(0) > 0.9

  - name: Search the skills corpus with a string the bot produced
    general_ludd.agent.gludd_embed:
      op: search
      corpus: skills
      text: "{{ bot_request }}"
      top_k: 3
    register: hit

  - name: Use the best-matching skill
    ansible.builtin.debug:
      msg: >-
        Best skill is {{ ansible_facts.gludd_embed.results[0].name }}
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
          - For C(op=similar): the snapshot with C(results) (ranked task types),
            C(query_embedding_dim), C(embedding_method), and optionally
            C(query_embedding).
          - For C(op=compare): the snapshot with C(similarity) (pairwise form) or
            C(matrix) (batch form), C(embedding_method), C(dim), and optionally
            C(embeddings).
          - For C(op=search): the snapshot with C(corpus), C(results) (ranked
            items, each with C(rank)/C(name)/C(source_text)/C(similarity_score)/
            C(metadata)), C(query_embedding_dim), C(embedding_method), and
            optionally C(query_embedding).
        type: dict
        returned: always
"""

from __future__ import annotations

from ansible.module_utils.basic import AnsibleModule
from ansible_collections.general_ludd.agent.plugins.module_utils.gludd import (
    GluddClient,
    error_result,
    ok_result,
)


def main() -> None:
    module = AnsibleModule(
        argument_spec=dict(
            op=dict(
                type="str",
                choices=["similar", "compare", "search"],
                default="similar",
            ),
            text=dict(type="str"),
            corpus=dict(
                type="str",
                choices=["skills", "task_types", "prompts", "traces", "events"],
                default="skills",
            ),
            text_a=dict(type="str"),
            text_b=dict(type="str"),
            texts=dict(type="list", elements="str"),
            top_k=dict(type="int", default=5),
            work_type=dict(type="str"),
            include_embedding=dict(type="bool", default=False),
            include_embeddings=dict(type="bool", default=False),
            daemon_url=dict(type="str", default="http://localhost:8000"),
            psk=dict(type="str", default="", no_log=True),
            timeout=dict(type="int", default=30),
        ),
        supports_check_mode=True,
    )

    op = module.params["op"]

    if op == "compare":
        text_a = module.params.get("text_a")
        text_b = module.params.get("text_b")
        texts = module.params.get("texts")
        has_pair = text_a is not None and text_b is not None
        has_batch = texts is not None and len(texts) >= 2
        if has_pair == has_batch:
            module.fail_json(
                **error_result(
                    "op=compare requires either (text_a AND text_b) OR "
                    "texts with len >= 2"
                )
            )
            return
        path = "/api/embeddings/compare"
        body: dict[str, object] = {
            "include_embeddings": module.params["include_embeddings"],
        }
        if has_pair:
            body["text_a"] = text_a
            body["text_b"] = text_b
        else:
            body["texts"] = texts
    elif op == "search":
        if not module.params.get("text"):
            module.fail_json(**error_result("op=search requires text"))
            return
        path = "/api/embeddings/search"
        body = {
            "text": module.params["text"],
            "corpus": module.params["corpus"],
            "top_k": module.params["top_k"],
            "include_embeddings": module.params["include_embeddings"],
        }
    else:  # op == "similar" (default, back-compat)
        if not module.params.get("text"):
            module.fail_json(
                **error_result("op=similar requires text")
            )
            return
        path = "/api/embeddings/similar"
        body = {
            "text": module.params["text"],
            "top_k": module.params["top_k"],
            "include_embedding": module.params["include_embedding"],
        }
        if module.params.get("work_type"):
            body["work_type"] = module.params["work_type"]

    client = GluddClient(
        base_url=module.params["daemon_url"],
        psk=module.params["psk"],
        timeout=module.params["timeout"],
    )

    resp = client.post(path, body=body)
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
