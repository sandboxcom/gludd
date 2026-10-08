"use strict";
var FreeLLMAPICandidateV0111 = (() => {
  var __defProp = Object.defineProperty;
  var __getOwnPropDesc = Object.getOwnPropertyDescriptor;
  var __getOwnPropNames = Object.getOwnPropertyNames;
  var __hasOwnProp = Object.prototype.hasOwnProperty;
  var __export = (target, all) => {
    for (var name in all)
      __defProp(target, name, { get: all[name], enumerable: true });
  };
  var __copyProps = (to, from, except, desc) => {
    if (from && typeof from === "object" || typeof from === "function") {
      for (let key of __getOwnPropNames(from))
        if (!__hasOwnProp.call(to, key) && key !== except)
          __defProp(to, key, { get: () => from[key], enumerable: !(desc = __getOwnPropDesc(from, key)) || desc.enumerable });
    }
    return to;
  };
  var __toCommonJS = (mod) => __copyProps(__defProp({}, "__esModule", { value: true }), mod);

  // candidate_v0_11_1_entry.ts
  var candidate_v0_11_1_entry_exports = {};
  __export(candidate_v0_11_1_entry_exports, {
    expectedReliability: () => expectedReliability
  });

  // scoring.ts
  var PEAK_EXEMPT_STRATEGIES = ["fastest", "reliable"];
  var TASK_EXEMPT_STRATEGIES = [...PEAK_EXEMPT_STRATEGIES, "custom"];
  var PRIOR_SUCCESS = 1;
  var PRIOR_FAILURE = 1;
  function reliabilityPosterior(successes, failures, community) {
    return {
      alpha: Math.max(0, successes) + (community?.successes ?? 0) + PRIOR_SUCCESS,
      beta: Math.max(0, failures) + (community?.failures ?? 0) + PRIOR_FAILURE
    };
  }
  function expectedReliability(successes, failures, community) {
    const { alpha, beta } = reliabilityPosterior(successes, failures, community);
    return alpha / (alpha + beta);
  }

  // candidate_v0_11_1_entry.ts
  function candidateBatch(payload) {
    const candidates = JSON.parse(payload);
    return JSON.stringify(candidates.map((candidate) => expectedReliability(
      candidate.successes,
      candidate.failures,
      {
        successes: candidate.community_successes,
        failures: candidate.community_failures
      }
    )));
  }
  Object.assign(globalThis, {
    __gludd_freellmapi_candidate_batch: candidateBatch
  });
  return __toCommonJS(candidate_v0_11_1_entry_exports);
})();
