# rag_example — RAG Pipeline Demo Role

Demonstrates Retrieval-Augmented Generation using `module_utils.rag.RAGPipeline`.

## What it does

1. **Ingests 4 travel brochures** (Paris, Tokyo, New York, Rome) into an in-memory vector store using `add_document`
2. **Runs 3 RAG queries** against the stored documents:
   - Best European cities for art museums
   - Best spring destinations with good transit
   - Best cities for culinary experiences
3. **Writes results** as JSON and Markdown to `artifact_dir`

## Module_utils used

| Module util | What it provides |
|---|---|
| `rag.py` | RAGPipeline, Chunker, VectorStore |
| `model_client.py` | ModelClient, Message — talks to the daemon |
| `embeddings.py` | HashEmbedder, cosine_similarity |

## How any collection can use this pattern

```python
from ansible_collections.general_ludd.agent.plugins.module_utils.rag import RAGPipeline
from ansible_collections.general_ludd.agent.plugins.module_utils.model_client import ModelClient

pipeline = RAGPipeline(model_client=ModelClient())
pipeline.add_document("document text here", {"source": "my_collection"})
answer = pipeline.query("your question here", top_k=5)
```

## Requirements

- A running gludd daemon at `daemon_url` for the query phase (model calls)
- Document ingestion (`add_document`) works offline with HashEmbedder

## Usage

```yaml
- name: Run RAG example
  ansible.builtin.include_role:
    name: general_ludd.agent.rag_example
  vars:
    daemon_url: "http://localhost:8000"
```
