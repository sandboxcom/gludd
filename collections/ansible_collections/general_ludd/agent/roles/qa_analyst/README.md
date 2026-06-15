# qa_analyst

Cross-cut quality verdict role. Slurps `test_matrix`, `coverage`, and `flaky`
sub-artifacts and computes a weighted QA score:

```
score = pass_weight * pass_rate
      + coverage_weight * (coverage_pct / 100)
      - flaky_penalty * min(flaky_count, 10) / 10
```

Verdicts:
- `pass` — score ≥ threshold AND no flaky tests
- `conditional` — score ≥ threshold BUT flaky tests present
- `fail` — score < threshold

## Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `daemon_url` | `http://localhost:8000` | Daemon URL for gludd_facts |
| `artifact_dir` | `/tmp/gludd-qa-analyst` | Output directory |
| `test_matrix_artifact` | `""` | Path to test_matrix.json |
| `coverage_artifact` | `""` | Path to coverage.json |
| `flaky_artifact` | `""` | Path to flaky.json |
| `pass_threshold` | `0.8` | Minimum score for non-fail verdict |
| `pass_weight` | `0.5` | Weight for pass rate in score |
| `coverage_weight` | `0.3` | Weight for coverage in score |
| `flaky_penalty` | `0.2` | Penalty weight for flaky tests |

## Artifacts

- `qa_analyst.json` — structured verdict with score, coverage_pct, flaky_count, matrix_gaps
- `qa_analyst.md` — human-readable score breakdown

## SAFE-BY-DEFAULT

Never mutates the repo. `enable_git_push: false`.
