// SPDX-License-Identifier: MIT
// Gludd-owned JSON adapter; scoring remains the exact imported upstream export.
import { expectedReliability } from "./scoring.js";

interface CandidateInput {
  successes: number;
  failures: number;
  community_successes: number;
  community_failures: number;
}

function candidateBatch(payload: string): string {
  const candidates = JSON.parse(payload) as CandidateInput[];
  return JSON.stringify(candidates.map((candidate) => expectedReliability(
    candidate.successes,
    candidate.failures,
    {
      successes: candidate.community_successes,
      failures: candidate.community_failures,
    },
  )));
}

Object.assign(globalThis, {
  __gludd_freellmapi_candidate_batch: candidateBatch,
});

export { expectedReliability };
