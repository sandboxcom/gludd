from general_ludd.memory.tempr_retriever import TEMPRRetriever, parse_temporal_expression


def test_tempr_retrieves_semantic_document_and_parses_time_window():
    retriever = TEMPRRetriever(max_workers=1)
    retriever.index(
        [
            {"id": "a", "content": "OpenCode plugin E2E configuration"},
            {"id": "b", "content": "garden notes"},
        ]
    )
    results = retriever.retrieve("OpenCode plugin", top_k=1)
    assert results[0].doc_id == "a"
    start, end = parse_temporal_expression("last 3 days")
    assert start is not None and end is not None
