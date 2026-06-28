# Trust + registry constraints for terraform plans.
#
# Loaded alongside core.rego by `conftest test -p infra/terraform/policies/`.
# Operators extend the allow-list per environment by editing data.json
# (data.gludd.provider_trust_list). The default allow-list mirrors the
# providers that ship under infra/terraform/modules/* — anything else must be
# deliberately added before a collection or stack may use it.

package main

# All providers must come from the canonical registry. Operators may override
# data.gludd.provider_registry in data.json if they mirror a private registry.
deny[level] {
	provider := input.configuration.provider_config[_]
	full := object.get(provider, "full_name", provider.name)
	not startswith(full, data.gludd.provider_registry)
	level := sprintf("provider %q is not from the canonical registry %q", [full, data.gludd.provider_registry])
}

# Every provider used must be in the operator's trust list.
deny[level] {
	provider := input.configuration.provider_config[_]
	full := object.get(provider, "full_name", provider.name)
	not provider_in_trust_list(full)
	level := sprintf("provider %q is not in the operator trust list (extend data.gludd.provider_trust_list)", [full])
}

provider_in_trust_list(name) {
	name == data.gludd.provider_trust_list[_]
}

# Short-name fallback (e.g. "aws" with no full_name) is checked against the
# name half of each trust-list entry: "hashicorp/aws" -> "aws".
provider_in_trust_list(name) {
	trust := data.gludd.provider_trust_list[_]
	endswith(trust, concat("/", ["", name]))
}
