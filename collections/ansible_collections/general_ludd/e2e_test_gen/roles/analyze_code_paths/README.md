# analyze_code_paths — extract public symbols from Python source

Extracts functions, classes, and methods from a target module via tree-sitter
AST walking. Produces a `ModuleSymbols` JSON artifact with line ranges and
public/private classification. Falls back to regex scan when tree-sitter is
unavailable.

Leverages `general_ludd.agents.test_generation.code_path_analyzer.CodePathAnalyzer`
for tree-sitter extraction and `general_ludd.code_intelligence.extractor.ASTBlockExtractor`
for code graph building.

## Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `target_module` | `""` | Path to Python module to analyze |
| `artifact_dir` | `/tmp/gludd-e2e-test-gen` | Output directory |
| `daemon_url` | `http://localhost:8000` | Daemon URL |

## Artifact

`module_symbols.json`:
```json
{
  "name": "/path/to/module.py",
  "functions": [{"name": "do_work", "line_start": 42, "line_end": 67, "is_public": true}],
  "classes": [{"name": "Worker", "line_start": 70, "line_end": 150, "is_public": true, "methods": [...]}]
}
```
