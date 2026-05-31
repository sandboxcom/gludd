package hottentot.config

import future.keywords.in

guardrail_layers_valid[guardrail] {
    guardrail := input.guardrail
    guardrail.config_layer == true
    guardrail.hook_layer == true
    guardrail.prompt_layer == true
}

tdd_enforced[behavior] {
    behavior := input.behavior
    behavior.tdd_enforced == true
}

commit_after_green[behavior] {
    behavior := input.behavior
    behavior.commit_after_green == true
}

evidence_required[behavior] {
    behavior := input.behavior
    behavior.evidence_required == true
}

command_patterns_valid[behavior] {
    behavior := input.behavior
    all_make(behavior.allowed_command_patterns)
}

all_make(patterns) {
    count([p | p := patterns[_]; not startswith(p, "make ")]) == 0
}

stop_conditions_valid[behavior] {
    behavior := input.behavior
    "missing_credentials" in behavior.stop_conditions
}
