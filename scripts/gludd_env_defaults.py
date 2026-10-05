"""Shared default values for GLUDD_ environment variables read by scripts.

Scripts that need the same fallback as another script import the constant
from here instead of hardcoding the literal twice, so the env-var audit
flags real drift instead of duplicated literals.
"""

TASK_TIMEOUT_MS_DEFAULT = "300000"
LIVENESS_MAX_AGE_DEFAULT = "300"
TODOWRITE_STATE_DEFAULT = "/tmp/gludd-todowrite-state.json"
DAEMON_URL_DEFAULT = "http://127.0.0.1:8000"
