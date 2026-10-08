# Log Analyzer — Usage Examples

This document shows how to configure and run the `log_analyzer` role for daily analysis, scheduled operation, and real-time event-loop integration.

## 1. Basic Configuration — Point at Log Directories

```yaml
# playbooks/analyze_logs.yml
- hosts: localhost
  gather_facts: true
  roles:
    - role: general_ludd.operations.log_analyzer
      vars:
        enable_model_call: true
        model_profile_error_cluster: "sonnet"
        model_profile_anomaly: "sonnet"
        model_profile_behavior: "sonnet"
        model_profile_perf_regression: "sonnet"
        model_profile_root_cause: "sonnet"
        log_source_dirs: "/var/log/gludd,/home/gludd/.gludd/logs"
        gate_log_dir: ".gate-logs"
        analysis_window_hours: 24
```

Run it:

```bash
cd /opt/gludd
.venv/bin/ansible-playbook -e 'psk=<your-psk>' playbooks/analyze_logs.yml
```

## 2. Chain-of-Thought Logging

The role automatically captures every model call's system prompt, user prompt, and full response to a separate chain-of-thought log file. This is the raw evidence — separate from the analysis output.

```yaml
# Defaults (adjust in your playbook vars):
log_analyzer_cot_enabled: true
log_analyzer_cot_log: "/tmp/gludd-log-analyzer/log_analyzer_cot.log"
```

The CoT log format:

```text
=== log_analyzer chain-of-thought log ===
started: 2026-07-12T14:30:00Z
analysis_window_hours: 24
log_source_dirs: /var/log/gludd,...

=== TASK: error_clustering @ 2026-07-12T14:30:05Z ===
MODEL: sonnet
PROMPT: You are a log analysis engine. Analyze...
RESPONSE: [{cluster_id: "abc123", error_pattern: ...}]
===
```

Set `log_analyzer_cot_enabled: false` to skip CoT logging.

## 3. Full Configuration with All Sources

```yaml
- hosts: localhost
  gather_facts: true
  roles:
    - role: general_ludd.operations.log_analyzer
      vars:
        enable_model_call: true
        daemon_url: "https://gludd.internal:8000"
        psk: "{{ vault_psk }}"

        # Log sources
        log_source_dirs: "/var/log/gludd,/tmp/gludd-*,/home/gludd/.gludd/logs"
        daemon_log_glob: "*.log"
        agent_log_glob: "agent-*.log"
        subagent_trace_glob: "subagent-*.trace"
        gate_log_dir: ".gate-logs"
        gate_log_glob: "*.log"

        # OpenShift (set to "" to skip)
        openshift_log_dir: "/var/log/containers"
        openshift_log_glob: "*.log"

        # Systemd journal
        systemd_service_name: "gludd"

        # SearX
        searx_log_enabled: true
        searx_log_path: "/var/log/searx"

        # Analysis tuning
        analysis_window_hours: 48
        analysis_max_tokens: 8192
        max_log_lines_per_chunk: 1000

        # Thresholds
        error_rate_spike_factor: 2.5
        slow_tick_threshold_seconds: 30
        dispatch_gap_threshold_minutes: 10
        idle_period_threshold_minutes: 5

        # Model profiles
        model_profile_error_cluster: "sonnet"
        model_profile_anomaly: "sonnet"
        model_profile_behavior: "opus"
        model_profile_perf_regression: "sonnet"
        model_profile_root_cause: "sonnet"

        # Chain-of-thought
        log_analyzer_cot_enabled: true
        log_analyzer_cot_log: "/var/log/gludd/analysis_cot.log"
```

## 4. Daily Analysis Cron

Schedule the log analyzer to run every day at 06:00 UTC:

```bash
# /etc/cron.d/gludd-log-analyzer
SHELL=/bin/bash
PATH=/opt/gludd/.venv/bin:/usr/local/bin:/usr/bin:/bin
PSK_FILE=/etc/gludd/psk

0 6 * * * gludd cd /opt/gludd && .venv/bin/ansible-playbook \
  -e "psk=$(cat $PSK_FILE)" \
  playbooks/analyze_logs.yml \
  > /var/log/gludd/log_analysis_cron.log 2>&1
```

## 5. Real-Time Event Loop Integration

The log analyzer can be triggered from the gludd event loop for continuous analysis:

```python
# Inside the gludd event loop tick handler or a scheduled job
import subprocess
from pathlib import Path

def run_log_analysis(psk: str, window_hours: int = 1) -> dict:
    """Run log_analyzer role via ansible-playbook and return parsed findings."""
    result = subprocess.run(
        [
            "ansible-playbook",
            "-e", f"psk={psk}",
            "-e", f"analysis_window_hours={window_hours}",
            "-e", "log_source_dirs=/var/log/gludd",
            "-e", "enable_model_call=true",
            "playbooks/analyze_logs.yml",
        ],
        capture_output=True,
        text=True,
        timeout=300,
        cwd="/opt/gludd",
    )

    report_path = Path("/tmp/gludd-log-analyzer/log_analysis_report.json")
    if report_path.exists():
        import json
        return json.loads(report_path.read_text())
    return {"status": "no_report", "rc": result.returncode}
```

For integration with the daemon's `/api/facts` endpoint, wrap the report parser in a daemon route and return the findings as structured facts. The event loop can then dispatch remediation subagents based on severity.

## 6. Incremental / Tail Analysis

For very large log sets, you can run analysis on just the most recent logs:

```yaml
# 1-hour analysis window, use for near-real-time monitoring
analysis_window_hours: 1
max_log_lines_per_chunk: 200
```

Run every hour via cron or the event loop. The report is incremental — each run only analyzes the trailing window.

## 7. Output Files

All output lands in `log_analyzer_output_dir` when it is set. Otherwise the
role falls back to `artifact_dir` (default `/tmp/gludd-log-analyzer`). The same
resolved directory is used for setup, the analyzer CLI, and every final
artifact so a caller-selected test or tenant directory cannot leak output into
the operator default.

| File | Description |
|---|---|
| `log_analysis_report.json` | Structured findings with severity, frequency, timestamps |
| `log_analysis_report.md` | Human-readable markdown summary |
| `log_analyzer_cot.log` | Chain-of-thought: every model call's prompt + response |
| `raw_log_corpus.txt` | Full raw log content fed to the model (for audit) |

## 8. Troubleshooting

| Symptom | Fix |
|---|---|
| No model analysis in report | Set `enable_model_call: true` |
| Empty log corpus | Check `log_source_dirs` — ensure directories exist and contain `.log` files |
| `journalctl: command not found` | Role gracefully skips systemd when command unavailable |
| Model call timeout | Increase `analysis_max_tokens` or reduce `max_log_lines_per_chunk` |
| PSK visible in logs | Set `no_log` via `psk` variable — the role already masks PSK on all daemon calls |

## 9. Ansible Compatibility Evidence

Gludd registers its structured event collector directly with ansible-core's
task queue manager. ansible-core 2.19 builds an internal callback-method map;
programmatically registered callbacks must initialize that map before the run
or task successes and failures can disappear even though the playbook returns
a nonzero code. Gludd feature-detects and initializes this API, then tests that
successful events arrive and failure events are not silently lost.

This compatibility guard follows the Ansible community's long-lived warning
that 2.19 includes plugin API changes and compatibility corner cases:
[Core 2.19 templating changes — preview and testing](https://forum.ansible.com/t/core-2-19-templating-changes-preview-and-testing/40759).
The older request for real-time task output also documents why callback-backed
progress is operationally important for long-running work:
[Expose realtime output from shell](https://github.com/ansible/ansible/issues/3887).
