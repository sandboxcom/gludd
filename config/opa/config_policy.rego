package hottentot.config

import rego.v1

guardrail_layers_valid contains guardrail if {
    guardrail := input.guardrail
    guardrail.config_layer == true
    guardrail.hook_layer == true
    guardrail.prompt_layer == true
}

tdd_enforced contains behavior if {
    behavior := input.behavior
    behavior.tdd_enforced == true
}

commit_after_green contains behavior if {
    behavior := input.behavior
    behavior.commit_after_green == true
}

evidence_required contains behavior if {
    behavior := input.behavior
    behavior.evidence_required == true
}

command_patterns_valid contains behavior if {
    behavior := input.behavior
    all_make(behavior.allowed_command_patterns)
}

all_make(patterns) if {
    count([p | p := patterns[_]; not startswith(p, "make ")]) == 0
}

stop_conditions_valid contains behavior if {
    behavior := input.behavior
    "missing_credentials" in behavior.stop_conditions
}
