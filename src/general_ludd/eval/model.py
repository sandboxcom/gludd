"""Model evaluation wrapper for G2 eval harness."""

from __future__ import annotations

from general_ludd.eval.schema import EvalCase
from general_ludd.models.gateway import ModelGateway


class ModelEvaluator:
    def __init__(
        self,
        gateway: ModelGateway,
        *,
        profile_id: str = "sonnet",
        dry_run: bool = False,
    ) -> None:
        self._gateway = gateway
        self._profile_id = profile_id
        self._dry_run = dry_run

    def generate_patch(self, case: EvalCase) -> str:
        prompt = self._build_prompt(case)
        if self._dry_run:
            return prompt
        response = self._gateway.call_model(
            self._profile_id, [{"role": "user", "content": prompt}]
        )
        return response.content

    def _build_prompt(self, case: EvalCase) -> str:
        files_section = "\n".join(
            f"--- {name} ---\n{content}"
            for name, content in case.input_files.items()
        )
        return (
            f"Task: {case.description}\n\n"
            f"Input files:\n{files_section}\n\n"
            "Generate a unified diff patch to complete this task. "
            "Output only the patch, no explanation."
        )
