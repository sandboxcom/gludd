"""LangGraph-based multi-agent consensus engine with parallel model calls.

Replaces the serial for-loop in ConsensusEngine.run_debate() with concurrent
model calls via ThreadPoolExecutor, orchestrated by langgraph's StateGraph
for the round/consensus/judge state machine.
"""

from __future__ import annotations

import logging
import operator
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Annotated, Any, TypedDict

from pydantic import BaseModel

log = logging.getLogger(__name__)


class AgentVerdict(BaseModel):
    """Structured output from a consensus debate agent.

    Replaces the raw-string parsing in ConsensusEngine with a typed model.
    """

    agent_index: int
    verdict: str  # "approve", "reject", "needs_changes"
    rationale: str
    round_num: int


_VERDICT_LITERALS = frozenset({"approve", "reject", "needs_changes"})


class ConsensusState(TypedDict):
    """State for the langgraph consensus debate graph.

    Only agent_verdicts uses a reducer (operator.add) so each parallel
    thread can append its verdict. All other keys are plain "last write wins".
    """

    agent_verdicts: Annotated[list[dict[str, Any]], operator.add]
    question: str
    context: str
    num_agents: int
    max_rounds: int
    current_round: int
    final_verdict: str
    confidence: float
    consensus: bool | None  # None = not yet, True = unanimous, False = deadlocked
    judge_ruling: bool
    judge_verdict: str
    judge_rationale: str
    _entered_judge: bool
    dissent_summary: str


def _parse_to_verdict(
    raw: str,
    agent_index: int,
    round_num: int,
) -> AgentVerdict:
    """Parse raw reviewer output into a structured AgentVerdict."""
    normalized = raw.strip().lower()
    verdict = "needs_changes"
    for line in normalized.splitlines():
        line = line.strip()
        if line in _VERDICT_LITERALS:
            verdict = line
            break
    rationale_start = raw.find("\n")
    rationale = raw[rationale_start + 1 :].strip() if rationale_start != -1 else ""
    return AgentVerdict(
        agent_index=agent_index,
        verdict=verdict,
        rationale=rationale,
        round_num=round_num,
    )


def _build_agent_prompt(
    question: str,
    context: str,
    agent_index: int,
    num_agents: int,
    round_num: int,
    dissent_summary: str,
) -> str:
    """Build a perspective-tagged prompt for a specific debate agent."""
    prompt = f"You are reviewer agent {agent_index + 1} of {num_agents}.\n"
    if context:
        prompt += f"\nContext:\n{context}\n"
    prompt += f"\nQuestion:\n{question}\n"
    prompt += (
        "\nEvaluate the return and respond with EXACTLY one verdict on the first "
        "line of your response: approve, reject, or needs_changes.\n"
        "Provide a brief rationale after the verdict line.\n"
    )
    if round_num > 1 and dissent_summary:
        prompt += (
            "\nIn the previous round, the agents were NOT unanimous. "
            f"Other agents reasoned as follows:\n{dissent_summary}\n"
            "Re-evaluate your verdict in light of these dissenting opinions.\n"
        )
    return prompt


def _check_unanimity(verdicts: list[dict[str, Any]]) -> str | None:
    """Return the verdict if all agents agree, or None if there is dissent."""
    if not verdicts:
        return None
    first: str = verdicts[0]["verdict"]
    for v in verdicts[1:]:
        if v["verdict"] != first:
            return None
    return first


def _compute_confidence(verdicts: list[dict[str, Any]]) -> float:
    """Compute confidence as fraction of agents agreeing with the majority verdict."""
    if not verdicts:
        return 0.0
    vlist = [v["verdict"] for v in verdicts]
    majority_count = max(vlist.count(v) for v in set(vlist))
    return majority_count / len(vlist)


class LangGraphConsensusEngine:
    """Parallel multi-agent debate engine using langgraph StateGraph.

    Each round calls all N agents concurrently via ThreadPoolExecutor
    (N parallel LLM calls). After all agents report, the consensus_check
    node determines whether to exit, continue to the next round, or
    invoke a tie-breaking judge.

    Preserves the same run_debate() interface as ConsensusEngine so it can
    be dropped in as a replacement.
    """

    def __init__(
        self,
        reviewer_callable: Any = None,
        judge_callable: Any = None,
    ) -> None:
        self._reviewer = reviewer_callable
        self._judge = judge_callable
        self._graph = self._build_graph() if reviewer_callable is not None else None

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def run_debate(
        self,
        question: str,
        context: str = "",
        *,
        num_agents: int = 3,
        max_rounds: int = 5,
    ) -> dict[str, Any]:
        """Run a multi-agent debate and return the consensus result.

        Returns the same dict shape as ConsensusEngine.run_debate().
        """
        if self._reviewer is None:
            return _no_reviewer_result()
        if not question.strip():
            return _empty_question_result()
        if num_agents < 1:
            num_agents = 1
        if max_rounds < 1:
            max_rounds = 1

        initial_state: ConsensusState = {
            "agent_verdicts": [],
            "question": question,
            "context": context,
            "num_agents": num_agents,
            "max_rounds": max_rounds,
            "current_round": 0,
            "final_verdict": "",
            "confidence": 0.0,
            "consensus": None,
            "judge_ruling": False,
            "judge_verdict": "",
            "judge_rationale": "",
            "_entered_judge": False,
            "dissent_summary": "",
        }

        try:
            assert self._graph is not None
            result: ConsensusState = self._graph.invoke(initial_state)
        except Exception as exc:
            log.error("LangGraph consensus execution failed: %s", exc)
            return {
                "consensus": False,
                "verdict": "error",
                "confidence": 0.0,
                "rounds": 0,
                "transcript": [],
                "agent_votes": [],
                "error": f"LangGraph execution error: {exc}",
            }

        return self._build_result(result)

    # ------------------------------------------------------------------
    # Graph construction
    # ------------------------------------------------------------------

    def _build_graph(self) -> Any:
        """Construct and compile the langgraph StateGraph."""
        from langgraph.graph import END, START, StateGraph

        reviewer = self._reviewer
        judge = self._judge

        builder = StateGraph(ConsensusState)

        # --- agent_round node: parallel model calls for all N agents ---

        def agent_round_node(state: ConsensusState) -> dict[str, Any]:
            round_num = state["current_round"] + 1
            n = state["num_agents"]
            question = state["question"]
            context = state["context"]
            dissent = state.get("dissent_summary", "")

            def _call_agent(i: int) -> AgentVerdict:
                prompt = _build_agent_prompt(
                    question, context, i, n, round_num, dissent
                )
                raw = reviewer(prompt)
                return _parse_to_verdict(raw, i, round_num)

            verdicts: list[dict[str, Any]] = []
            with ThreadPoolExecutor(max_workers=n) as executor:
                futures = {executor.submit(_call_agent, i): i for i in range(n)}
                for future in as_completed(futures):
                    verdicts.append(future.result().model_dump())

            # Sort by agent_index for deterministic ordering
            verdicts.sort(key=lambda v: v["agent_index"])
            return {
                "agent_verdicts": verdicts,
                "current_round": round_num,
            }

        # --- consensus_check node ---

        def consensus_check_node(state: ConsensusState) -> dict[str, Any]:
            round_num = state["current_round"]
            n = state["num_agents"]
            verdicts = state["agent_verdicts"][-n:]

            unanimous = _check_unanimity(verdicts)
            if unanimous is not None:
                return {
                    "consensus": True,
                    "final_verdict": unanimous,
                    "confidence": _compute_confidence(verdicts),
                }

            if round_num >= state["max_rounds"]:
                return {"consensus": False}

            dissent = "\n".join(
                f"Agent {v['agent_index'] + 1}: {v['verdict']}"
                for v in verdicts
            )
            return {"dissent_summary": dissent}

        def route_after_check(state: ConsensusState) -> str:
            c = state["consensus"]
            if c is True:
                return "end"
            if c is False:
                return "judge"
            return "agent_round"

        # --- judge node ---

        def judge_node(state: ConsensusState) -> dict[str, Any]:
            n = state["num_agents"]
            verdicts = state["agent_verdicts"][-n:]
            if judge is None:
                return {
                    "final_verdict": "tie",
                    "confidence": _compute_confidence(verdicts),
                    "judge_ruling": False,
                    "_entered_judge": True,
                }

            judge_prompt = _build_judge_prompt(state, verdicts)
            judge_raw = judge(judge_prompt)
            judge_verdict, judge_rationale = _parse_judge_output(judge_raw)
            return {
                "final_verdict": judge_verdict,
                "confidence": 0.5,
                "judge_ruling": True,
                "judge_verdict": judge_verdict,
                "judge_rationale": judge_rationale,
                "_entered_judge": True,
            }

        # --- wiring ---

        builder.add_node("agent_round", agent_round_node)
        builder.add_node("consensus_check", consensus_check_node)
        builder.add_node("judge", judge_node)

        builder.add_edge(START, "agent_round")
        builder.add_edge("agent_round", "consensus_check")
        builder.add_conditional_edges(
            "consensus_check",
            route_after_check,
            {"end": END, "judge": "judge", "agent_round": "agent_round"},
        )
        builder.add_edge("judge", END)

        return builder.compile()

    # ------------------------------------------------------------------
    # Result assembly
    # ------------------------------------------------------------------

    def _build_result(self, state: ConsensusState) -> dict[str, Any]:
        """Convert the raw graph state into the canonical run_debate result dict."""
        agent_verdicts: list[dict[str, Any]] = state.get("agent_verdicts", [])
        rounds = state["current_round"]

        verdict = state.get("final_verdict", "needs_changes")
        confidence = state.get("confidence", 0.0)
        consensus = state.get("consensus") is True

        transcript = _build_transcript(agent_verdicts)

        result: dict[str, Any] = {
            "consensus": consensus,
            "verdict": verdict,
            "confidence": confidence,
            "rounds": rounds,
            "transcript": transcript,
            "agent_votes": agent_verdicts,
        }

        if state.get("_entered_judge") is True:
            result["judge_ruling"] = state.get("judge_ruling", False)
            result["judge_verdict"] = state.get("judge_verdict", "")
            result["judge_rationale"] = state.get("judge_rationale", "")

        return result


def _build_transcript(
    verdicts: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Group per-agent verdicts into per-round transcript entries."""
    if not verdicts:
        return []
    rounds: dict[int, list[dict[str, Any]]] = {}
    for v in verdicts:
        r = v.get("round_num", 0)
        rounds.setdefault(r, []).append(
            {"agent_index": v.get("agent_index", 0), "verdict": v.get("verdict", "")}
        )
    return [
        {"round": r, "votes": votes} for r, votes in sorted(rounds.items())
    ]


def _build_judge_prompt(
    state: ConsensusState,
    verdicts: list[dict[str, Any]],
) -> str:
    """Build a prompt for the tie-breaking judge."""
    parts = [
        "You are a tie-breaking judge. The debate agents could NOT reach consensus.",
        "",
        "Question:",
        state["question"],
    ]
    context = state.get("context", "")
    if context:
        parts.extend(["", "Context:", context])
    parts.append("")
    parts.append("Votes from the final round:")
    for v in verdicts:
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


def _parse_judge_output(raw: str) -> tuple[str, str]:
    """Parse a judge's raw output into verdict + rationale."""
    normalized = raw.strip().lower()
    verdict = "needs_changes"
    for line in normalized.splitlines():
        line = line.strip()
        if line in _VERDICT_LITERALS:
            verdict = line
            break
    rationale_start = raw.find("\n")
    rationale = raw[rationale_start + 1 :].strip() if rationale_start != -1 else ""
    return verdict, rationale


# ------------------------------------------------------------------
# Error / edge-case results
# ------------------------------------------------------------------


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
