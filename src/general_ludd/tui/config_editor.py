"""TUI configuration editor with menu navigation and overlay-file writes."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class MenuItem:
    label: str
    key: str
    value: Any = None
    item_type: str = "str"
    submenu: list[MenuItem] = field(default_factory=list)
    help_text: str = ""
    overlay_path: str = ""

    @property
    def is_menu(self) -> bool:
        return len(self.submenu) > 0


@dataclass
class ConfigCategory:
    name: str
    menu_items: list[MenuItem]
    overlay_path: str = ""


class ConfigEditor:
    def __init__(self, config_dir: str | None = None) -> None:
        self._config_dir = config_dir or os.path.expanduser("~/.config/gludd")
        self._overlay_dir = os.path.join(self._config_dir, "fs")

    def get_categories(self) -> list[ConfigCategory]:
        return [
            ConfigCategory(
                name="Database",
                overlay_path=self._overlay_path_for("database.yml"),
                menu_items=[
                    MenuItem(label="Engine", key="engine", value="sqlite", item_type="str",
                             help_text="sqlite or postgresql"),
                    MenuItem(label="URL", key="url", value="sqlite+aiosqlite://", item_type="str",
                             help_text="Database connection URL"),
                    MenuItem(label="Host", key="host", value="localhost", item_type="str"),
                    MenuItem(label="Port", key="port", value=5432, item_type="int"),
                    MenuItem(label="WAL Mode", key="wal", value=True, item_type="bool",
                             help_text="Write-Ahead Logging for SQLite"),
                ],
            ),
            ConfigCategory(
                name="Model Routing",
                overlay_path=self._overlay_path_for("model_routing.yml"),
                menu_items=[
                    MenuItem(label="Default Profile", key="default_profile", value="default", item_type="str",
                             help_text="Default model profile to use"),
                    MenuItem(label="Adaptive Routing", key="adaptive", value=True, item_type="bool",
                             help_text="Auto-select best model based on benchmarks"),
                    MenuItem(label="Max Cost USD", key="max_cost", value=0.01, item_type="float",
                             help_text="Maximum cost per API call"),
                ],
            ),
            ConfigCategory(
                name="Process Isolation",
                overlay_path=self._overlay_path_for("process_isolation.yml"),
                menu_items=[
                    MenuItem(label="Enabled", key="enabled", value=False, item_type="bool"),
                    MenuItem(label="Container Runtime", key="runtime", value="podman", item_type="str",
                             help_text="podman or docker"),
                    MenuItem(label="Memory Limit MB", key="memory_mb", value=512, item_type="int"),
                    MenuItem(label="CPU Shares", key="cpu_shares", value=1024, item_type="int"),
                ],
            ),
            ConfigCategory(
                name="Binary Paths",
                overlay_path=self._overlay_path_for("binary_paths.yml"),
                menu_items=[
                    MenuItem(label="OpenTofu", key="opentofu", value="tofu", item_type="str"),
                    MenuItem(label="OpenBao", key="openbao", value="bao", item_type="str"),
                    MenuItem(label="Podman", key="podman", value="podman", item_type="str"),
                    MenuItem(label="Docker", key="docker", value="docker", item_type="str"),
                    MenuItem(label="Ansible", key="ansible_playbook", value="ansible-playbook", item_type="str"),
                    MenuItem(label="UV", key="uv", value="uv", item_type="str"),
                    MenuItem(label="Git", key="git", value="git", item_type="str"),
                ],
            ),
            ConfigCategory(
                name="Budget",
                overlay_path=self._overlay_path_for("budget.yml"),
                menu_items=[
                    MenuItem(label="Daily Limit USD", key="daily_limit", value=10.0, item_type="float"),
                    MenuItem(label="Monthly Limit USD", key="monthly_limit", value=100.0, item_type="float"),
                    MenuItem(label="Per-Task Limit", key="per_task_limit", value=0.50, item_type="float"),
                    MenuItem(label="Alert Threshold", key="alert_pct", value=80, item_type="int",
                             help_text="Alert when budget reaches this percentage"),
                ],
            ),
            ConfigCategory(
                name="Secrets",
                overlay_path=self._overlay_path_for("secrets.yml"),
                menu_items=[
                    MenuItem(label="Backend", key="backend", value="env", item_type="str",
                             help_text="env or openbao"),
                    MenuItem(label="OpenBao URL", key="openbao_url", value="http://localhost:8200", item_type="str"),
                    MenuItem(label="OpenBao Mode", key="openbao_mode", value="external", item_type="str",
                             help_text="external or local-container"),
                    MenuItem(label="Auth Method", key="auth_method", value="approle", item_type="str"),
                ],
            ),
            ConfigCategory(
                name="AI Provider Keys",
                overlay_path=self._overlay_path_for("ai_providers.yml"),
                menu_items=[
                    MenuItem(label="Z.AI", key="zai", item_type="menu", submenu=[
                        MenuItem(label="API Key", key="ZAI_API_KEY", value="", item_type="str"),
                        MenuItem(label="Base URL", key="ZAI_BASE_URL",
                                 value="https://open.bigmodel.cn/api/paas/v4", item_type="str"),
                        MenuItem(label="Default Model", key="ZAI_MODEL", value="auto", item_type="str",
                                 help_text="auto = adaptive router picks best model"),
                    ]),
                    MenuItem(label="OpenRouter", key="openrouter", item_type="menu", submenu=[
                        MenuItem(label="API Key", key="OPENROUTER_API_KEY", value="", item_type="str"),
                        MenuItem(label="Base URL", key="OPENROUTER_BASE_URL",
                                 value="https://openrouter.ai/api/v1", item_type="str"),
                        MenuItem(label="Default Model", key="OPENROUTER_MODEL",
                                 value="auto", item_type="str",
                                 help_text="auto = route to best benchmarked model"),
                        MenuItem(label="Max Cost USD", key="openrouter_max_cost", value=0.50, item_type="float"),
                    ]),
                    MenuItem(label="OpenCode", key="opencode", item_type="menu", submenu=[
                        MenuItem(label="API Key", key="OPENCODE_API_KEY", value="", item_type="str"),
                        MenuItem(label="Default Model", key="opencode_model",
                                 value="auto", item_type="str",
                                 help_text="auto = adaptive router selection"),
                    ]),
                    MenuItem(label="OpenAI", key="openai", item_type="menu", submenu=[
                        MenuItem(label="API Key", key="OPENAI_API_KEY", value="", item_type="str"),
                        MenuItem(label="Base URL", key="OPENAI_BASE_URL",
                                 value="https://api.openai.com/v1", item_type="str"),
                        MenuItem(label="Default Model", key="openai_model", value="auto", item_type="str",
                                 help_text="auto = use best scored model for task type"),
                        MenuItem(label="Max Tokens", key="openai_max_tokens", value=4096, item_type="int"),
                    ]),
                    MenuItem(label="Anthropic", key="anthropic", item_type="menu", submenu=[
                        MenuItem(label="API Key", key="ANTHROPIC_API_KEY", value="", item_type="str"),
                        MenuItem(label="Base URL", key="ANTHROPIC_BASE_URL",
                                 value="https://api.anthropic.com/v1", item_type="str"),
                        MenuItem(label="Default Model", key="anthropic_model",
                                 value="auto", item_type="str",
                                 help_text="auto = adaptive routing based on task"),
                        MenuItem(label="Max Tokens", key="anthropic_max_tokens", value=4096, item_type="int"),
                    ]),
                    MenuItem(label="HuggingFace", key="huggingface", item_type="menu", submenu=[
                        MenuItem(label="API Token", key="HF_TOKEN", value="", item_type="str"),
                        MenuItem(label="Cache Dir", key="HF_HOME",
                                 value="~/.cache/huggingface", item_type="str"),
                    ]),
                    MenuItem(label="Together AI", key="together", item_type="menu", submenu=[
                        MenuItem(label="API Key", key="TOGETHER_API_KEY", value="", item_type="str"),
                        MenuItem(label="Base URL", key="TOGETHER_BASE_URL",
                                 value="https://api.together.xyz/v1", item_type="str"),
                        MenuItem(label="Default Model", key="together_model",
                                 value="auto", item_type="str",
                                 help_text="auto = adaptive router best pick"),
                    ]),
                    MenuItem(label="Slurm", key="slurm", item_type="menu", submenu=[
                        MenuItem(label="API URL", key="SLURM_API_URL", value="", item_type="str",
                                 help_text="Slurm REST API endpoint"),
                        MenuItem(label="Auth Token", key="SLURM_AUTH_TOKEN", value="", item_type="str",
                                 help_text="JWT or Slurm auth token"),
                        MenuItem(label="Default Partition", key="SLURM_PARTITION", value="gpu", item_type="str"),
                        MenuItem(label="Default Account", key="SLURM_ACCOUNT", value="", item_type="str"),
                        MenuItem(label="QOS", key="SLURM_QOS", value="normal", item_type="str"),
                        MenuItem(label="GPU Count", key="SLURM_GPU_COUNT", value=1, item_type="int"),
                        MenuItem(label="GPU Type", key="SLURM_GPU_TYPE", value="a100", item_type="str"),
                        MenuItem(label="Time Limit", key="SLURM_TIME", value="04:00:00", item_type="str"),
                        MenuItem(label="Memory GB", key="SLURM_MEM_GB", value=32, item_type="int"),
                    ]),
                ],
            ),
            ConfigCategory(
                name="Cloud Credentials",
                overlay_path=self._overlay_path_for("cloud_creds.yml"),
                menu_items=[
                    MenuItem(label="AWS", key="aws", item_type="menu", submenu=[
                        MenuItem(label="Access Key ID", key="AWS_ACCESS_KEY_ID", value="", item_type="str"),
                        MenuItem(label="Secret Access Key", key="AWS_SECRET_ACCESS_KEY", value="", item_type="str"),
                        MenuItem(label="Default Region", key="AWS_DEFAULT_REGION", value="us-east-1", item_type="str"),
                        MenuItem(label="Terraform Backend", key="AWS_TF_BACKEND", value="s3", item_type="str"),
                        MenuItem(label="Instance Type", key="aws_instance_type", value="g5.xlarge", item_type="str"),
                    ]),
                    MenuItem(label="Azure", key="azure", item_type="menu", submenu=[
                        MenuItem(label="Client ID", key="ARM_CLIENT_ID", value="", item_type="str"),
                        MenuItem(label="Client Secret", key="ARM_CLIENT_SECRET", value="", item_type="str"),
                        MenuItem(label="Tenant ID", key="ARM_TENANT_ID", value="", item_type="str"),
                        MenuItem(label="Subscription ID", key="ARM_SUBSCRIPTION_ID", value="", item_type="str"),
                        MenuItem(label="Resource Group", key="azure_resource_group", value="gludd-rg", item_type="str"),
                        MenuItem(label="Location", key="azure_location", value="eastus", item_type="str"),
                        MenuItem(label="VM Size", key="azure_vm_size", value="Standard_NC6s_v3", item_type="str"),
                        MenuItem(label="Use MSI", key="ARM_USE_MSI", value=False, item_type="bool"),
                    ]),
                    MenuItem(label="GCP", key="gcp", item_type="menu", submenu=[
                        MenuItem(label="Project ID", key="GOOGLE_PROJECT", value="", item_type="str"),
                        MenuItem(label="Credentials", key="GOOGLE_APPLICATION_CREDENTIALS",
                                 value="", item_type="str"),
                        MenuItem(label="Region", key="gcp_region", value="us-central1", item_type="str"),
                        MenuItem(label="Zone", key="gcp_zone", value="us-central1-a", item_type="str"),
                        MenuItem(label="Machine Type", key="gcp_machine_type", value="n1-standard-4", item_type="str"),
                        MenuItem(label="GPU Type", key="gcp_gpu_type", value="nvidia-tesla-t4", item_type="str"),
                    ]),
                ],
            ),
        ]

    def read_yaml(self, path: str) -> dict[str, Any]:
        import yaml
        p = Path(path)
        if p.exists():
            with open(p) as f:
                data = yaml.safe_load(f)
            return data if isinstance(data, dict) else {}
        return {}

    def write_overlay(self, path: str, data: dict[str, Any]) -> None:
        import yaml
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        with open(p, "w") as f:
            yaml.dump(data, f, default_flow_style=False, allow_unicode=True)

    def _overlay_path_for(self, name: str) -> str:
        return os.path.join(self._overlay_dir, name)
