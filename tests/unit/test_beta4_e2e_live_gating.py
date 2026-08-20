"""Regression contracts for bounded beta4 live-model E2E selection."""

from pathlib import Path

ROOT = Path(__file__).parents[2]


def _class_prefix(source: str, class_name: str, width: int = 420) -> str:
    marker = f"class {class_name}:"
    class_offset = source.index(marker)
    return source[max(0, class_offset - width) : class_offset]


def test_game_pipeline_downloads_require_explicit_live_opt_in() -> None:
    source = (ROOT / "tests/e2e/test_game_dev_full_pipeline.py").read_text()
    prefix = _class_prefix(source, "TestGameDevFullPipeline")

    assert '_LIVE_MODEL_E2E = os.environ.get("GLUDD_LIVE_MODEL_E2E") == "1"' in source
    assert "@pytest.mark.skipif(" in prefix
    assert "not _LIVE_MODEL_E2E" in prefix


def test_model_matrix_download_classes_require_explicit_live_opt_in() -> None:
    source = (ROOT / "tests/e2e/test_model_matrix_pipeline.py").read_text()

    assert '_LIVE_MODEL_E2E = os.environ.get("GLUDD_LIVE_MODEL_E2E") == "1"' in source
    for class_name in ("TestLocalModelMatrixDownloadServe", "TestCodingModelAsCoderRole"):
        prefix = _class_prefix(source, class_name)
        assert "@pytest.mark.skipif(" in prefix
        assert "not _LIVE_MODEL_E2E" in prefix


def test_dedicated_game_pipeline_target_enables_live_mode() -> None:
    makefile = (ROOT / "Makefile").read_text()
    start = makefile.index("test-e2e-game-pipeline:")
    recipe = makefile[start : makefile.index("\n\n", start)]

    assert 'GLUDD_LIVE_MODEL_E2E="1"' in recipe


def test_model_matrix_probe_closes_endpoint_response() -> None:
    source = (ROOT / "tests/e2e/test_model_matrix_pipeline.py").read_text()

    assert "with urllib.request.urlopen(req, timeout=5):" in source


def test_generic_model_matrix_does_not_import_local_inference_runtime() -> None:
    source = (ROOT / "tests/e2e/test_model_matrix_pipeline.py").read_text()
    deps_body = source[source.index("def _deps_reason()") : source.index("_LOCAL_DEPS_SKIP =")]

    assert "if not _LIVE_MODEL_E2E:" in deps_body
    assert deps_body.index("if not _LIVE_MODEL_E2E:") < deps_body.index("_has_llama_cpp()")
