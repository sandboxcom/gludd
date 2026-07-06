"""G11 Multi-agent debate/consensus engine."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any


class ConsensusEngine:
    """Orchestrates a multi-agent debate to converge on a consensus position.

    Uses dependency injection: the reviewer and judge are plain callables,
    making the engine trivially testable without importing concrete classes.
    """

    def __init__(
        self,
        reviewer: Callable[[str], str] | None = None,
        judge: Callable[[str], str] | None = None,
    ) -> None:
        self._reviewer = reviewer
        self._judge = judge

    def run_debate(
        self,
        question: str,
        context: str = "",
        *,
        num_agents: int = 3,
        max_rounds: int = 5,
    ) -> dict[str, Any]:
        """Run a multi-agent debate on a question and return the consensus.

        Args:
            question: The question or proposition to debate.
            context: Additional context to include in the prompt.
            num_agents: Number of agents participating in the debate.
            max_rounds: Maximum number of debate rounds before forced consensus.

        Returns:
            A dictionary with consensus position, confidence, and transcript.
        """
        if self._reviewer is None:
            return _no_reviewer_result()
        if not question.strip():
            return _empty_question_result()
        if num_agents < 1:
            num_agents = 1
        if max_rounds < 1:
            max_rounds = 1

        transcript: list[dict[str, Any]] = []

        for round_num in range(1, max_rounds + 1):
            votes, round_transcript = _run_round(
                self._reviewer,
                question,
                context,
                num_agents,
                round_num,
                previous_rounds=transcript,
            )
            transcript.append(round_transcript)

            result = _check_consensus(votes)
            if result is not None:
                return {
                    "consensus": True,
                    "verdict": result,
                    "confidence": _compute_confidence(votes),
                    "rounds": round_num,
                    "transcript": transcript,
                    "agent_votes": [
                        {
                            "agent_index": i,
                            "verdict": v["verdict"],
                            "rationale": v["rationale"],
                            "round": v["round"],
                        }
                        for i, v in enumerate(votes)
                    ],
                }

        return _tie_or_judge(
            self._judge, votes, question, context, transcript, max_rounds
        )


def _run_round(
    reviewer: Callable[[str], str],
    question: str,
    context: str,
    num_agents: int,
    round_num: int,
    previous_rounds: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Run one debate round and return votes + round transcript."""
    votes: list[dict[str, Any]] = []
    dissent_text = ""
    if previous_rounds:
        dissent_text = _build_dissent_prompt(previous_rounds[-1])

    for i in range(num_agents):
        prompt = _build_prompt(
            question, context, i, num_agents, round_num, dissent_text
        )
        raw = reviewer(prompt)
        verdict, rationale = _parse_verdict(raw)
        votes.append(
            {
                "agent_index": i,
                "verdict": verdict,
                "rationale": rationale,
                "raw": raw,
                "round": round_num,
            }
        )

    round_transcript = {
        "round": round_num,
        "votes": [
            {"agent_index": v["agent_index"], "verdict": v["verdict"]}
            for v in votes
        ],
    }
    return votes, round_transcript


def _build_prompt(
    question: str,
    context: str,
    agent_index: int,
    num_agents: int,
    round_num: int,
    dissent_text: str,
) -> str:
    prompt = f"You are reviewer agent {agent_index + 1} of {num_agents}.\n"
    if context:
        prompt += f"\nContext:\n{context}\n"
    prompt += f"\nQuestion:\n{question}\n"
    prompt += (
        "\nEvaluate the return and respond with EXACTLY one verdict on the first "
        "line of your response: approve, reject, or needs_changes.\n"
        "Provide a brief rationale after the verdict line.\n"
    )
    if round_num > 1 and dissent_text:
        prompt += (
            "\nIn the previous round, the agents were NOT unanimous. "
            f"Other agents reasoned as follows:\n{dissent_text}\n"
            "Re-evaluate your verdict in light of these dissenting opinions.\n"
        )
    return prompt


def _build_dissent_prompt(round_transcript: dict[str, Any]) -> str:
    """Build a summary of dissenting opinions from the prior round's votes."""
    votes = round_transcript.get("votes", [])
    parts: list[str] = []
    for v in votes:
        parts.append(
            f"Agent {v.get('agent_index', -1) + 1}: {v.get('verdict', 'unknown')}"
        )
    return "\n".join(parts)


def _parse_verdict(raw: str) -> tuple[str, str]:
    """Parse a reviewer's raw output into a normalized verdict + rationale."""
    normalized = raw.strip().lower()
    verdict = "needs_changes"
    for line in normalized.splitlines():
        line = line.strip()
        if line in ("approve", "reject", "needs_changes"):
            verdict = line
            break
    rationale_start = raw.find("\n")
    rationale = (
        raw[rationale_start + 1 :].strip() if rationale_start != -1 else ""
    )
    return verdict, rationale


def _check_consensus(votes: list[dict[str, Any]]) -> str | None:
    """Return the verdict if all agents agree, or None if there is dissent."""
    if not votes:
        return None
    first: str = votes[0]["verdict"]
    for vote in votes[1:]:
        if vote["verdict"] != first:
            return None
    return first


def _compute_confidence(votes: list[dict[str, Any]]) -> float:
    """Compute confidence as the fraction of agents agreeing with the majority."""
    if not votes:
        return 0.0
    verdicts = [v["verdict"] for v in votes]
    majority_count = max(verdicts.count(v) for v in set(verdicts))
    return majority_count / len(verdicts)


def _tie_or_judge(
    judge: Callable[[str], str] | None,
    votes: list[dict[str, Any]],
    question: str,
    context: str,
    transcript: list[dict[str, Any]],
    max_rounds: int,
) -> dict[str, Any]:
    """Resolve a deadlocked debate via a judge model or return a tie."""
    if judge is None:
        return _tie_result(votes, transcript, max_rounds)

    judge_prompt = _build_judge_prompt(question, context, votes)
    judge_raw = judge(judge_prompt)
    judge_verdict, _ = _parse_verdict(judge_raw)

    return {
        "consensus": False,
        "verdict": judge_verdict,
        "confidence": 0.5,
        "rounds": max_rounds,
        "transcript": transcript,
        "agent_votes": [
            {
                "agent_index": v["agent_index"],
                "verdict": v["verdict"],
                "rationale": v.get("rationale", ""),
                "round": v["round"],
            }
            for v in votes
        ],
        "judge_ruling": True,
        "judge_verdict": judge_verdict,
        "judge_rationale": judge_raw,
    }


def _tie_result(
    votes: list[dict[str, Any]],
    transcript: list[dict[str, Any]],
    max_rounds: int,
) -> dict[str, Any]:
    return {
        "consensus": False,
        "verdict": "tie",
        "confidence": max(_compute_confidence(votes) if votes else 0.0, 0.0),
        "rounds": max_rounds,
        "transcript": transcript,
        "agent_votes": [
            {
                "agent_index": v["agent_index"],
                "verdict": v["verdict"],
                "rationale": v.get("rationale", ""),
                "round": v["round"],
            }
            for v in votes
        ],
        "judge_ruling": False,
    }


def _build_judge_prompt(
    question: str,
    context: str,
    votes: list[dict[str, Any]],
) -> str:
    parts = [
        "You are a tie-breaking judge. The debate agents could NOT reach consensus.",
        "",
        "Question:",
        question,
    ]
    if context:
        parts.extend(["", "Context:", context])
    parts.append("")
    parts.append("Votes from the final round:")
    for v in votes:
        parts.append(
            f"  Agent {v['agent_index'] + 1}: {v['verdict']} — "
            f"{v.get('rationale', '')}"
        )
    parts.append("")
    parts.append(
        "Make a final ruling. Respond with EXACTLY one verdict on the first "
        "line: approve, reject, or needs_changes."
    )
    parts.append("Provide your reasoning after the verdict line.")
    return "\n".join(parts)


def _no_reviewer_result() -> dict[str, Any]:
    return {
        "consensus": False,
        "verdict": "error",
        "confidence": 0.0,
        "rounds": 0,
        "transcript": [],
        "agent_votes": [],
        "error": "No reviewer configured",
    }


def _empty_question_result() -> dict[str, Any]:
    return {
        "consensus": False,
        "verdict": "error",
        "confidence": 0.0,
        "rounds": 0,
        "transcript": [],
        "agent_votes": [],
        "error": "Question must not be empty",
    }
