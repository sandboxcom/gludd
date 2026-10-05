// SPDX-License-Identifier: MIT
// Pure scoring subset adapted from FreeLLMAPI commit
// 780a7d8d6dcbc818eb10ec17da210635b569ae22.

"use strict";

const PRIOR_SUCCESS = 1;
const PRIOR_FAILURE = 1;
const SPEED_SCALE_TOK_S = 60;
const TTFB_BEST_MS = 300;
const TTFB_WORST_MS = 5000;
const THROUGHPUT_WEIGHT = 0.6;
const TTFB_WEIGHT = 0.4;
const SPEED_PRIOR = 0.6;
const HEADROOM_FLOOR = 0.1;
const HEADROOM_RAMP_START = 0.2;
const MAX_PENALTY = 10;
const RATE_LIMIT_MAX_DAMP = 0.6;

function clamp(value, lower, upper) {
  return Math.min(upper, Math.max(lower, value));
}

function reliabilityPosterior(successes, failures, community) {
  return {
    alpha: Math.max(0, successes) + community.successes + PRIOR_SUCCESS,
    beta: Math.max(0, failures) + community.failures + PRIOR_FAILURE,
  };
}

function expectedReliability(posterior) {
  return posterior.alpha / (posterior.alpha + posterior.beta);
}

function throughputScore(tokensPerSecond) {
  if (tokensPerSecond <= 0) {
    return 0;
  }
  return 1 - Math.exp(-tokensPerSecond / SPEED_SCALE_TOK_S);
}

function firstTokenScore(ttfbMs) {
  if (ttfbMs <= TTFB_BEST_MS) {
    return 1;
  }
  if (ttfbMs >= TTFB_WORST_MS) {
    return 0;
  }
  return 1 - (ttfbMs - TTFB_BEST_MS) / (TTFB_WORST_MS - TTFB_BEST_MS);
}

function speedScore(tokensPerSecond, ttfbMs) {
  const noThroughput = tokensPerSecond <= 0;
  const noFirstToken = ttfbMs === null;
  if (noThroughput && noFirstToken) {
    return SPEED_PRIOR;
  }
  const throughput = throughputScore(tokensPerSecond);
  if (noFirstToken) {
    return throughput;
  }
  const firstToken = firstTokenScore(ttfbMs);
  if (noThroughput) {
    return firstToken;
  }
  return THROUGHPUT_WEIGHT * throughput + TTFB_WEIGHT * firstToken;
}

function headroomRamp(remaining) {
  const bounded = clamp(remaining, 0, 1);
  if (bounded >= HEADROOM_RAMP_START) {
    return 1;
  }
  return HEADROOM_FLOOR
    + (1 - HEADROOM_FLOOR) * (bounded / HEADROOM_RAMP_START);
}

function headroomFactor(used, budget) {
  if (budget <= 0) {
    return 1;
  }
  return headroomRamp(1 - used / budget);
}

function rateWindowHeadroomFactor(usedFraction) {
  if (usedFraction === null || !Number.isFinite(usedFraction)) {
    return 1;
  }
  return headroomRamp(1 - usedFraction);
}

function rateLimitFactor(penalty) {
  const bounded = clamp(penalty, 0, MAX_PENALTY);
  return 1 - (bounded / MAX_PENALTY) * RATE_LIMIT_MAX_DAMP;
}

function __gludd_freellmapi_factor_batch(payload) {
  const candidates = JSON.parse(payload);
  const factors = candidates.map((candidate) => {
    const posterior = reliabilityPosterior(
      candidate.successes,
      candidate.failures,
      {
        successes: candidate.community_successes,
        failures: candidate.community_failures,
      },
    );
    return {
      candidate_identity_digest: candidate.candidate_identity_digest,
      reliability_alpha: posterior.alpha,
      reliability_beta: posterior.beta,
      expected_reliability: expectedReliability(posterior),
      speed: speedScore(candidate.tokens_per_second, candidate.ttfb_ms),
      headroom: headroomFactor(candidate.used_tokens, candidate.budget_tokens),
      rate_window_headroom: rateWindowHeadroomFactor(
        candidate.rate_window_used_fraction,
      ),
      rate_limit: rateLimitFactor(candidate.rate_limit_penalty),
    };
  });
  return JSON.stringify(factors);
}

globalThis.__gludd_freellmapi_factor_batch = __gludd_freellmapi_factor_batch;
