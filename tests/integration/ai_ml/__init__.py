"""Integration tests for the general_ludd.ai_ml expert collection.

Exercises the wired-together pipeline (ExpertRequest -> ExpertRouter ->
EvidenceStore -> answer_question -> ExpertResult) against the acceptance
criteria in docs/specs/FEATURE_AI_ML_EXPERT.md §16 (AIML-AT-001 through
AIML-AT-007). Unlike the unit tests in tests/unit/test_ai_ml_*.py which
isolate individual modules, these tests drive multiple components through
their public entry points in the same scenario.
"""
