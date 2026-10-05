#!/usr/bin/python
# Copyright: Agentic Harness
# SPDX-License-Identifier: MIT
"""
DOCUMENTATION:
  module: gludd_rag
  short_description: Retrieval-Augmented Generation pipeline via module_utils.rag
  description:
    - With C(op=add_document) ingests text into an in-memory vector store
      backed by the L(rag module_utils,ansible_collections.general_ludd.agent.plugins.module_utils.rag).
    - With C(op=query) embeds a question, retrieves the top-k closest chunks,
      builds a prompt with them as context, and asks the daemon model gateway
      for an answer.
    - The pipeline uses the pluggable embedder from C(rag.py) (HashEmbedder by
      default, or a daemon-backed embedder when the daemon is reachable).
    - Check-mode safe for C(op=query) — returns a placeholder answer.
  options:
    op:
      description: Which RAG operation to run.
      type: str
      choices: [add_document, query]
      default: query
    text:
      description:
        - Document text to ingest when C(op=add_document), or the question
          to answer when C(op=query).
      type: str
      required: true
    metadata:
      description: Key-value metadata attached to the document (C(op=add_document) only).
      type: dict
      default: {}
    top_k:
      description: Number of closest chunks to retrieve for context (C(op=query) only).
      type: int
      default: 5
    chunk_size:
      description: Target chunk size in characters.
      type: int
      default: 1000
    chunk_overlap:
      description: Overlap between chunks in characters.
      type: int
      default: 200
    daemon_url:
      description: Base URL of the daemon (for model calls in C(op=query)).
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
      default: 120

EXAMPLES:
  - name: Add travel brochures to the RAG store
    general_ludd.agent.gludd_rag:
      op: add_document
      text: |
        Paris: The City of Light. Visit the Eiffel Tower, Louvre Museum,
        and Notre-Dame Cathedral. Best visited April-June or September-October.
        Famous for croissants, café culture, and the Seine river cruises.
      metadata:
        source: travel_brochure
        destination: Paris
        region: Europe
    register: paris_doc

  - name: Query the RAG pipeline for travel recommendations
    general_ludd.agent.gludd_rag:
      op: query
      text: "What are the best cities in Europe for art museums?"
      top_k: 3
    register: rag_answer

  - name: Display the answer
    ansible.builtin.debug:
      msg: "{{ rag_answer.answer }}"

RETURN:
  answer:
    description: Model-generated answer using retrieved context (C(op=query)).
    type: str
    returned: when op=query
  retrieved_chunks:
    description: The context chunks that were retrieved and fed to the model.
    type: list
    returned: when op=query
  chunk_count:
    description: Number of chunks created from the document (C(op=add_document)).
    type: int
    returned: when op=add_document
  stored_total:
    description: Total vector entries in the store after the operation.
    type: int
    returned: always
"""

from __future__ import annotations

import os
from typing import Any

from ansible.module_utils.basic import AnsibleModule
from ansible_collections.general_ludd.agent.plugins.module_utils.gludd import (
    error_result,
    ok_result,
)
from ansible_collections.general_ludd.agent.plugins.module_utils.model_client import (
    ModelClient,
)
from ansible_collections.general_ludd.agent.plugins.module_utils.rag import RAGPipeline


def main() -> None:
    module = AnsibleModule(
        argument_spec=dict(
            op=dict(
                type="str",
                choices=["add_document", "query"],
                default="query",
            ),
            text=dict(type="str", required=True),
            metadata=dict(type="dict", default={}),
            top_k=dict(type="int", default=5),
            chunk_size=dict(type="int", default=1000),
            chunk_overlap=dict(type="int", default=200),
            daemon_url=dict(type="str", default="http://localhost:8000"),
            psk=dict(type="str", default="", no_log=True),
            timeout=dict(type="int", default=120),
        ),
        supports_check_mode=True,
    )

    op = module.params["op"]
    text = module.params["text"]
    metadata = module.params["metadata"]
    top_k = module.params["top_k"]
    chunk_size = module.params["chunk_size"]
    chunk_overlap = module.params["chunk_overlap"]

    daemon_url = module.params["daemon_url"]
    psk = module.params["psk"]
    timeout = module.params["timeout"]

    if daemon_url:
        os.environ["GLUDD_DAEMON_URL"] = daemon_url
    if psk:
        os.environ["GLUDD_AUTH_PSK"] = psk
    if timeout:
        os.environ["GLUDD_MODEL_TIMEOUT"] = str(timeout)

    model_client = ModelClient()

    pipeline = RAGPipeline(
        model_client=model_client,
        chunk_size=chunk_size,
        chunk_overlap=chunk_overlap,
    )

    if op == "add_document":
        chunks = pipeline.add_document(text, metadata=metadata)
        module.exit_json(
            **ok_result(
                {
                    "chunk_count": len(chunks),
                    "stored_total": pipeline.stored_count,
                },
                changed=True,
            )
        )
        return

    if module.check_mode:
        module.exit_json(
            **ok_result(
                {
                    "answer": "[check-mode: query skipped]",
                    "retrieved_chunks": [],
                    "stored_total": pipeline.stored_count,
                },
                changed=False,
            )
        )
        return

    try:
        answer = pipeline.query(text, top_k=top_k)
    except Exception as exc:
        module.fail_json(**error_result(f"RAG query failed — is the daemon reachable? {exc}"))
        return

    results: list[dict[str, Any]] = []
    query_vec = pipeline._embedder.embed(text)
    for entry in pipeline._store.search(query_vec, top_k=top_k):
        results.append(
            {
                "text": entry.chunk.text[:300],
                "metadata": entry.chunk.metadata,
                "index": entry.chunk.index,
            }
        )

    module.exit_json(
        **ok_result(
            {
                "answer": answer,
                "retrieved_chunks": results,
                "stored_total": pipeline.stored_count,
            },
            changed=True,
        )
    )


if __name__ == "__main__":
    main()
