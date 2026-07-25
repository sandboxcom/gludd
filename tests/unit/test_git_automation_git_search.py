import general_ludd.git_automation.git_search as git_search


def test_search_git_history_serializes_indexer_results(monkeypatch):
    received = {}

    class Result:
        def to_dict(self):
            return {"hash": "abc"}

    class Indexer:
        def __init__(self, **kwargs):
            received["init"] = kwargs

        def search(self, **kwargs):
            received["search"] = kwargs
            return [Result()]

    monkeypatch.setattr(git_search, "GitHistoryIndexer", Indexer)
    result = git_search.search_git_history("fix", author="Ada", limit=2, repo_path="/repo")
    assert result == [{"hash": "abc"}]
    assert received["init"]["repo_path"] == "/repo"
    assert received["search"]["author"] == "Ada"
