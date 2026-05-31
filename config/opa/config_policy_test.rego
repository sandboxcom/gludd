package hottentot.config

test_guardrail_layers_valid {
    guardrail_layers_valid with input as {
        "guardrail": {"config_layer": true, "hook_layer": true, "prompt_layer": true}
    }
}

test_tdd_enforced {
    tdd_enforced with input as {
        "behavior": {"tdd_enforced": true}
    }
}

test_commit_after_green {
    commit_after_green with input as {
        "behavior": {"commit_after_green": true}
    }
}

test_command_patterns_valid {
    command_patterns_valid with input as {
        "behavior": {"allowed_command_patterns": ["make *"]}
    }
}

test_stop_conditions_valid {
    stop_conditions_valid with input as {
        "behavior": {"stop_conditions": ["missing_credentials", "environment_change"]}
    }
}
