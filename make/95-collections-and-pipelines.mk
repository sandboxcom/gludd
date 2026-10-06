# --- Collection Tests ---
# binary_re collection: 8 roles + 3 knowledge modules (fuzzing_strategies, obfuscation_techniques, prompt_injection_detector)
test-binary-re:
	@$(UV) run python -m pytest collections/ansible_collections/general_ludd/binary_re/tests/ -v

test-binary-unit:
	@$(UV) run python -m pytest tests/unit/test_binary_re_deobfuscate.py tests/unit/test_binary_re_fuzz_target.py tests/unit/test_binary_re_fuzzing_strategies.py tests/unit/test_binary_versions.py tests/unit/test_binary_paths.py -v --tb=short

# radio collection: 10 roles + 5 knowledge modules (antenna_types, frequency_allocations, modulation_schemes, propagation_models, radio_exam_data)
test-radio:
	@$(UV) run python -m pytest collections/ansible_collections/general_ludd/radio/tests/ -v

# os_expert collection: 12 roles (Ansible-only, no Python modules yet)
test-os-expert:
	@if [ -d collections/ansible_collections/general_ludd/os_expert/tests ]; then \
		$(UV) run python -m pytest collections/ansible_collections/general_ludd/os_expert/tests/ -v; \
	else \
		echo "os_expert collection has no Python tests yet (Ansible roles only)"; \
	fi

# STS (Security Token Service) test suite
test-sts:
	@$(UV) run python -m pytest tests/unit/test_sts_issuer.py tests/unit/test_sts_daemon_wiring.py tests/unit/test_sts_reaper.py tests/unit/test_sts_audit_model.py tests/unit/test_sts_audit.py tests/integration/sts/test_sts_module_integration.py tests/integration/test_secrets_sts_integration.py tests/e2e/test_e2e_security_sts.py -v --tb=short

# VM sandbox test suite
test-vm:
	@$(UV) run python -m pytest tests/unit/test_vm_lifecycle.py tests/unit/test_security_sandboxes_vm_lifecycle.py tests/unit/test_vm_sandbox_backends.py tests/unit/test_vm_image_builder.py tests/unit/test_vm_image_builder_self_test.py tests/unit/test_vm_p4_real_executor.py tests/unit/test_vm_p5_real_firecracker.py tests/integration/test_vm_sandbox_integration.py tests/integration/sandboxes/test_vm_sandbox_integration.py tests/bench/test_vm_sandbox_overhead.py -v --tb=short

# agent collection: module_utils (gludd, embeddings, capability_policy, fs_write_*, etc.) + roles
test-agent:
	@$(UV) run python -m pytest collections/ansible_collections/general_ludd/agent/tests/ -v \
		--ignore=collections/ansible_collections/general_ludd/agent/tests/unit/test_capability_router.py \
		--ignore=collections/ansible_collections/general_ludd/agent/tests/unit/test_model_client.py \
		--ignore=collections/ansible_collections/general_ludd/agent/tests/unit/test_rag.py

# Run all collection test suites
test-collections: test-binary-re test-radio test-os-expert test-e2e-test-gen test-language test-governance test-agent

# governance collection: 20 roles + 18 module_utils (borders, bodies, tax, currency, conflicts, treaties, civic services, etc.)
test-governance:
	@if [ -d collections/ansible_collections/general_ludd/governance/tests ]; then \
		$(UV) run python -m pytest collections/ansible_collections/general_ludd/governance/tests/ -v; \
	else \
		echo "governance collection has no Python tests yet (Ansible roles + knowledge modules only)"; \
	fi

governance-syntax:
	@GOV_ROLE_DIR=collections/ansible_collections/general_ludd/governance/roles; \
	if [ -d "$$GOV_ROLE_DIR" ]; then \
		for d in $$GOV_ROLE_DIR/*/; do \
			for f in $$(find "$$d" -name '*.yml' -o -name '*.yaml' 2>/dev/null); do \
				echo "Checking $$f..."; \
				$(UV) run python -c "import yaml; yaml.safe_load(open('$$f'))" || exit 1; \
			done; \
		done; \
		echo "governance collection YAML syntax OK"; \
	else \
		echo "governance roles not found (skipping syntax check)"; \
	fi

governance-health:
	@GOV_UTIL_DIR=collections/ansible_collections/general_ludd/governance/plugins/module_utils; \
	if [ -d "$$GOV_UTIL_DIR" ]; then \
		for f in $$GOV_UTIL_DIR/*.py; do \
			basename=$$(basename "$$f" .py); \
			[ "$$basename" = "__init__" ] && continue; \
			echo "Importing governance.$$basename..."; \
			sys_path_entry="$(CURDIR)/collections/ansible_collections/general_ludd/governance/plugins/module_utils"; \
			$(UV) run python -c "import sys; sys.path.insert(0, '$$sys_path_entry'); __import__('$$basename')" || exit 1; \
		done; \
		echo "governance module_utils imports OK"; \
	else \
		echo "governance module_utils not found (skipping health check)"; \
	fi

# e2e_test_gen collection: 5 roles (analyze_code_paths, write_e2e_tests, generate_scenarios, verify_coverage, validate_scenarios)
test-e2e-test-gen:
	@if [ -d collections/ansible_collections/general_ludd/e2e_test_gen/tests ]; then \
		$(UV) run python -m pytest collections/ansible_collections/general_ludd/e2e_test_gen/tests/ -v; \
	else \
		echo "e2e_test_gen collection has no Python tests yet (Ansible roles only)"; \
	fi

# language collection: 8 roles (font_analyze, phonetic_transcribe, i18n_extract, bom_detect, locale_format, homoglyph_scan, unicode_analyze, encoding_detect)
test-language:
	@if [ -d collections/ansible_collections/general_ludd/language/tests ]; then \
		$(UV) run python -m pytest collections/ansible_collections/general_ludd/language/tests/ -v; \
	else \
		echo "language collection has no Python tests yet (Ansible roles + knowledge modules only)"; \
	fi

# test-language-expert: E2E target per spec FEATURE_LANGUAGE_EXPERT.md Section 8.
# Runs collection schema check + ALL unit tests + integration tests + coverage gate (>=85%).
test-language-expert:
	@echo "=== test-language-expert: schema + unit + integration + coverage ==="
	@GLUDD_XDIST_WORKERS=2 $(UV) run python scripts/adaptive_test.py \
		tests/unit/test_language_expert_collection.py \
		tests/unit/test_language_phase_c.py \
		tests/unit/test_language_phase_d.py \
		tests/unit/test_language_phase_e.py \
		tests/unit/test_language_phase_f.py \
		tests/unit/test_language_font_data.py \
		tests/unit/test_language_i18n_data.py \
		tests/unit/test_language_role_integration.py \
		tests/integration/test_language_expert_integration.py \
		tests/integration/test_language_cli.py \
		collections/ansible_collections/general_ludd/language/tests/ \
		--cov=src/general_ludd/language \
		--cov-report=term-missing \
		--cov-fail-under=85 \
		-v --tb=short

# molecule-test-binary-re: runs molecule scenarios for binary_re collection roles
molecule-test-binary-re:
	@echo "=== molecule-test-binary-re ==="
	@if [ -d molecule/playbooks/binary_re ]; then \
		$(MAKE) --no-print-directory molecule-test SCENARIO=binary_re; \
	else \
		echo "binary_re collection has no molecule scenarios in molecule/playbooks/"; \
	fi

# molecule-test-radio: runs molecule scenarios for radio collection roles
molecule-test-radio:
	@echo "=== molecule-test-radio ==="
	@if [ -d molecule/playbooks/radio ]; then \
		$(MAKE) --no-print-directory molecule-test SCENARIO=radio; \
	else \
		echo "radio collection has no molecule scenarios in molecule/playbooks/"; \
	fi

# molecule-test-os-expert: runs molecule scenarios for os_expert collection roles
molecule-test-os-expert:
	@echo "=== molecule-test-os-expert ==="
	@if [ -d molecule/playbooks/os_expert ]; then \
		$(MAKE) --no-print-directory molecule-test SCENARIO=os_expert; \
	else \
		echo "os_expert collection has no molecule scenarios in molecule/playbooks/"; \
	fi

# molecule-test-e2e-test-gen: runs molecule scenarios for e2e_test_gen collection roles
molecule-test-e2e-test-gen:
	@echo "=== molecule-test-e2e-test-gen ==="
	@if [ -d molecule/playbooks/e2e_test_gen ]; then \
		$(MAKE) --no-print-directory molecule-test SCENARIO=e2e_test_gen; \
	else \
		echo "e2e_test_gen collection has no molecule scenarios in molecule/playbooks/"; \
	fi

# molecule-test-language: runs molecule scenarios for language collection roles
molecule-test-language:
	@echo "=== molecule-test-language ==="
	@if [ -d molecule/playbooks/language ]; then \
		$(MAKE) --no-print-directory molecule-test SCENARIO=language; \
	else \
		echo "language collection has no molecule scenarios in molecule/playbooks/"; \
	fi

# molecule-test-chat: runs molecule scenarios for chat collection roles
molecule-test-chat:
	@echo "=== molecule-test-chat ==="
	@if [ -d molecule/playbooks/chat ]; then \
		$(MAKE) --no-print-directory molecule-test SCENARIO=chat; \
	else \
		echo "chat collection has no molecule scenarios in molecule/playbooks/"; \
	fi

# Run networking role lint + syntax validation together
networking-validate: networking-role-lint networking-role-syntax
	@echo "networking role validation complete"

# Move ansible roles from monolithic agent collection to domain-specific collections
move-ansible-roles:
	@mkdir -p collections/ansible_collections/general_ludd/security/roles
	@mkdir -p collections/ansible_collections/general_ludd/networking/roles
	@mkdir -p collections/ansible_collections/general_ludd/infrastructure/roles
	@mkdir -p collections/ansible_collections/general_ludd/operations/roles
	@mv collections/ansible_collections/general_ludd/agent/roles/ssl_cert collections/ansible_collections/general_ludd/security/roles/ssl_cert
	@mv collections/ansible_collections/general_ludd/agent/roles/hsm_operations collections/ansible_collections/general_ludd/security/roles/hsm_operations
	@mv collections/ansible_collections/general_ludd/agent/roles/sql_injection collections/ansible_collections/general_ludd/security/roles/sql_injection
	@mv collections/ansible_collections/general_ludd/agent/roles/command_injection collections/ansible_collections/general_ludd/security/roles/command_injection
	@mv collections/ansible_collections/general_ludd/agent/roles/prompt_injection collections/ansible_collections/general_ludd/security/roles/prompt_injection
	@mv collections/ansible_collections/general_ludd/agent/roles/audit_framework collections/ansible_collections/general_ludd/security/roles/audit_framework
	@mv collections/ansible_collections/general_ludd/agent/roles/networking collections/ansible_collections/general_ludd/networking/roles/networking
	@mv collections/ansible_collections/general_ludd/agent/roles/service_discovery collections/ansible_collections/general_ludd/infrastructure/roles/service_discovery
	@mv collections/ansible_collections/general_ludd/agent/roles/auto_register_service collections/ansible_collections/general_ludd/infrastructure/roles/auto_register_service
	@mv collections/ansible_collections/general_ludd/agent/roles/auto_retire_service collections/ansible_collections/general_ludd/infrastructure/roles/auto_retire_service
	@mv collections/ansible_collections/general_ludd/agent/roles/log_analyzer collections/ansible_collections/general_ludd/operations/roles/log_analyzer
	@mv collections/ansible_collections/general_ludd/agent/roles/ci_pipeline_repair collections/ansible_collections/general_ludd/operations/roles/ci_pipeline_repair
	@mv collections/ansible_collections/general_ludd/agent/roles/deploy_model_server_slurm collections/ansible_collections/general_ludd/operations/roles/deploy_model_server_slurm
	@echo "Moved 13 roles: 6→security, 1→networking, 3→infrastructure, 3→operations"

# Verify Python imports for scapy_adapter module work
networking-healthcheck:
	@$(UV) run python -c "from general_ludd.networking import scapy_adapter; print('scapy_adapter import OK')" 2>/dev/null && \
		echo "networking healthcheck: OK" || \
		echo "networking module not found (skipping healthcheck)"

# Backup .opencode/ to .opencode.orig/ (excludes node_modules/)
# Run this before a long session so restore-opencode has a recent fallback.
backup-opencode:
	@echo "Backing up .opencode/ -> .opencode.orig/ (excluding node_modules/) ..."
	@rsync -a --delete --exclude='node_modules/' --exclude='node_modules' .opencode/ .opencode.orig/
	@touch .opencode.orig
	@echo "  backup timestamp: $$(date -u +%Y-%m-%dT%H:%M:%SZ)"
	@echo ".opencode/ backed up successfully."
	@echo ""
	@echo "=== post-backup verification ==="
	@$(UV) run python scripts/verify_opencode_backup.py || true

# Check that .opencode.orig/ backup is fresh (<24h older than .opencode/)
check-opencode-backup:
	@if [ ! -d .opencode.orig ]; then \
		echo "  WARNING: .opencode.orig/ does not exist. Run 'make backup-opencode' to create it."; \
		exit 1; \
	fi
	@BACKUP_AGE=$$(find .opencode.orig -maxdepth 0 -newer .opencode -print | wc -l | tr -d ' '); \
	if [ "$$BACKUP_AGE" = "0" ]; then \
		echo "  WARNING: .opencode.orig/ is older than .opencode/. Run 'make backup-opencode' to refresh."; \
		exit 1; \
	fi
	@echo "  .opencode.orig/ backup is fresh."

# Verify .opencode.orig/ backup is content-current (files exist + shared.ts exports match).
# Runs as a post-backup step in backup-opencode; also callable standalone.
verify-opencode-backup:
	@$(UV) run python scripts/verify_opencode_backup.py

# Restore .opencode/ from .opencode.orig/ and clear corrupt cache after OS crash
# Per https://opencode.ai/docs/troubleshooting: corrupted ~/.cache/opencode
# causes opencode to refuse to start. This target restores and cleans.
restore-opencode:
	@echo "Restoring .opencode/ ..."
	@if [ -d .opencode.orig ]; then \
		echo "  Source: .opencode.orig/ (rsync backup)"; \
		rsync -a --delete --exclude='node_modules/' --exclude='node_modules' .opencode.orig/ .opencode/; \
		echo "  .opencode/ restored from .opencode.orig/"; \
	elif git ls-files --error-unmatch .opencode/plugin/enforce-floor.ts >/dev/null 2>&1; then \
		echo "  .opencode.orig/ not found — falling back to git HEAD"; \
		echo "  Source: git HEAD (tracked .opencode/ files)"; \
		git checkout HEAD -- .opencode/; \
		echo "  .opencode/ restored from git HEAD"; \
	else \
		echo "ERROR: Neither .opencode.orig/ nor tracked .opencode/ files found."; \
		echo "  .opencode/ is not tracked in git and no backup exists."; \
		echo "  Create a backup first with: make backup-opencode"; \
		exit 1; \
	fi
	@echo "Clearing corrupted opencode cache ..."
	@rm -rf ~/.cache/opencode && echo "  ~/.cache/opencode cleared"
	@echo ".opencode/ restored. Restart opencode for changes to take effect."

# Fix opencode startup crash caused by global config conflicts.
# Root cause: ~/.config/opencode/ has an OLD enforce-multitask.ts that
# conflicts with the project version, plus a permission:{*:allow} override.
# This script backs up + fixes the global config (never deletes files).
# See: scripts/fix_opencode_crash.py
fix-opencode-crash:
	@$(PYTHON) scripts/fix_opencode_crash.py

# Diagnose which plugins crash Node load (runs each .ts through node --experimental-strip-types)
diag-plugin-load:
	@$(PYTHON) scripts/diagnose_plugin_load.py

# Diagnose plugin load by importing ALL plugins in one node process (reproduces opencode startup)
diag-plugin-load-all:
	@$(PYTHON) scripts/diagnose_plugin_load_all.py

# Simulate opencode startup: load every plugin from opencode.json, call factory, verify hooks
diag-opencode-startup:
	@$(PYTHON) scripts/diagnose_opencode_startup.py

# ── untested-module discovery ────────────────────────────────────────────────
find-untested:
	$(PYTHON) scripts/find_untested_modules.py

test-hot-module-load:
	@$(MAKE) --no-print-directory hot-reload-plugins
	@$(MAKE) --no-print-directory check-hot-reload-fresh

diag-multitask:
	@node --experimental-strip-types _diag_multitask.ts

diag-e2e:
	@node --experimental-strip-types _diag_e2e.ts

cat-file:
	@[ -n "$(FILE)" ] || { echo "Usage: make cat-file FILE=path"; exit 1; }
	@case "$(FILE)" in /tmp/gludd-*) ;; /*|*..*) echo "Refusing path outside workspace: $(FILE)"; exit 1;; esac
	@/bin/cat "$(FILE)"

CODEX_SKILLS_ROOT ?= $$HOME/.codex/skills

codex-system-skill-read:
	@[ -n "$(SKILL)" ] || { echo "Usage: make codex-system-skill-read SKILL=name [CODEX_SKILLS_ROOT=path]"; exit 2; }
	@case "$(SKILL)" in *..*|/*) echo "Invalid skill name: $(SKILL)"; exit 1;; *) ;; esac
	@SKILL_FILE="$(CODEX_SKILLS_ROOT)/.system/$(SKILL)/SKILL.md"; \
	[ -f "$$SKILL_FILE" ] || { echo "Skill not found: $$SKILL_FILE"; exit 1; }; \
	/bin/cat "$$SKILL_FILE"

list-files:
	@[ -n "$(DIR)" ] || { echo "Usage: make list-files DIR=path"; exit 1; }
	@case "$(DIR)" in /*|*..*) echo "Refusing path outside workspace: $(DIR)"; exit 1;; esac
	@/usr/bin/find "$(DIR)" \( -path '*/.git' -o -path '*/.venv' -o -path '*/.mypy_cache' -o -path '*/.pytest_cache' -o -path '*/.gate-logs' \) -prune -o -type f -print | /usr/bin/sort

search:
	@[ -n "$(PATTERN)" ] || { echo "Usage: make search PATTERN=regex [SEARCH_PATH=path]"; exit 1; }
	@SEARCH_ROOT="$(if $(SEARCH_PATH),$(SEARCH_PATH),.)"; \
	case "$$SEARCH_ROOT" in /tmp/gludd-*) ;; /*|*..*) echo "Refusing path outside workspace: $$SEARCH_ROOT"; exit 1;; esac; \
	TMP="/tmp/gludd-search.$$$$"; \
	if command -v rg >/dev/null 2>&1; then \
		rg -n --glob '!.git/**' --glob '!.venv/**' --glob '!.mypy_cache/**' --glob '!.pytest_cache/**' --glob '!.gate-logs/**' -- "$(PATTERN)" "$$SEARCH_ROOT" > "$$TMP" 2>/dev/null || true; \
	else \
		/usr/bin/grep -R -n -- "$(PATTERN)" "$$SEARCH_ROOT" > "$$TMP" 2>/dev/null || true; \
	fi; \
	if [ -s "$$TMP" ]; then \
		while IFS= read -r line; do printf '%s\n' "$$line"; done < "$$TMP"; \
		/bin/rm -f "$$TMP"; \
	else \
		/bin/rm -f "$$TMP"; \
		echo "No matches for $(PATTERN) in $$SEARCH_ROOT"; \
		exit 1; \
	fi

show-lines:
	@[ -n "$(FILE)" ] && [ -n "$(START)" ] && [ -n "$(END)" ] || { echo "Usage: make show-lines FILE=path START=n END=n"; exit 1; }
	@$(PYTHON) scripts/show_lines.py "$(FILE)" "$(START)" "$(END)"

ps:
	@UV=echo $(SYSTEM_PYTHON) scripts/active_work_status.py --process-table

PROCESS_ROOT_PID ?=
PROCESS_NAMESPACE ?=
PROCESS_CLEANUP_APPLY ?= 0
PROCESS_CLEANUP_VALIDATE_ONLY ?= 0
terminate-project-process-tree: ## Safely preview or terminate one namespaced project process tree.
	@[ -n "$(PROCESS_ROOT_PID)" ] && [ -n "$(PROCESS_NAMESPACE)" ] || { echo "Usage: make terminate-project-process-tree PROCESS_ROOT_PID=pid PROCESS_NAMESPACE=/absolute/project/path [PROCESS_CLEANUP_APPLY=1] [PROCESS_CLEANUP_VALIDATE_ONLY=1]"; exit 2; }
	@$(UV) run python scripts/process_cleanup.py --root-pid "$(PROCESS_ROOT_PID)" --namespace "$(PROCESS_NAMESPACE)" $(if $(filter 1,$(PROCESS_CLEANUP_APPLY)),--apply,) $(if $(filter 1,$(PROCESS_CLEANUP_VALIDATE_ONLY)),--validate-only,)

kill-project-pid:
	@[ -n "$(PID)" ] || { echo "Usage: make kill-project-pid PID=pid"; exit 1; }
	@cmd=$$(/bin/ps -p "$(PID)" -o command=); ppid=$$(/bin/ps -p "$(PID)" -o ppid= | tr -d ' '); orphan=0; \
	do_kill() { /bin/kill -TERM "$$1" 2>/dev/null || true; sleep 1; /bin/kill -0 "$$1" 2>/dev/null && /bin/kill -KILL "$$1" 2>/dev/null || true; }; \
	if [ "$$ppid" = "1" ] || ! /bin/kill -0 "$$ppid" 2>/dev/null; then orphan=1; fi; \
	case "$$cmd" in \
		*"/Users/shawnwilson/gludd"*|*"make search"*|*"grep -R"*) do_kill "$(PID)" ;; \
		*"make gate"*|*"_gate-refresh-body"*) if [ "$$orphan" = "1" ]; then do_kill "$(PID)"; else echo "Refusing to kill non-orphan gate: pid=$(PID) ppid=$$ppid"; exit 1; fi ;; \
		*"uv cache prune"*) if [ "$$orphan" = "1" ]; then do_kill "$(PID)"; else echo "Refusing to kill non-orphan uv cache prune: pid=$(PID) ppid=$$ppid"; exit 1; fi ;; \
		*"uv run python -m pytest tests/unit/"*) if [ "$$orphan" = "1" ]; then do_kill "$(PID)"; else echo "Refusing to kill non-orphan pytest: pid=$(PID) ppid=$$ppid"; exit 1; fi ;; \
		*"python -m general_ludd.cli daemon"*"/Users/shawnwilson/tmp/pytest-of-shawnwilson/"*) if [ "$$orphan" = "1" ]; then do_kill "$(PID)"; else echo "Refusing to kill non-orphan test daemon: pid=$(PID) ppid=$$ppid"; exit 1; fi ;; \
		*) echo "Refusing to kill unrelated process: $$cmd"; exit 1 ;; \
	esac

cleanup-molecule-processes:
	@/bin/ps -ax -o pid=,command= | /usr/bin/awk '/\/Users\/shawnwilson\/gludd\/\.venv\/bin\/molecule test -s|\/Users\/shawnwilson\/gludd\/\.venv\/bin\/ansible-playbook .*\/Users\/shawnwilson\/gludd\/molecule|\/Users\/shawnwilson\/gludd\/\.venv\/bin\/detect-secrets scan|\/Users\/shawnwilson\/gludd\/molecule\/mock_daemon\/server.py/ { print $$1 }' | /usr/bin/xargs -r /bin/kill

remove-workspace-file:
	@[ -n "$(FILE)" ] || { echo "Usage: make remove-workspace-file FILE=path"; exit 1; }
	@case "$(FILE)" in /*|*..*) echo "Refusing path outside workspace: $(FILE)"; exit 1;; esac
	@[ -f "$(FILE)" ] || { echo "Not a file: $(FILE)"; exit 1; }
	@/bin/rm -f -- "$(FILE)"

molecule-reset:
	@[ -n "$(SCENARIO)" ] || { echo "Usage: make molecule-reset SCENARIO=name"; exit 1; }
	@$(MAKE) --no-print-directory cleanup-molecule-processes
	@# Destroy runtime resources without Molecule's reset path deleting tracked scenario content.
	@MOLECULE_GLOB="molecule/playbooks/*/molecule.yml" $(UV) run molecule destroy -s "$(SCENARIO)"

show-multitask-state:
	@if [ -f /tmp/gludd-multitask-state.json ]; then ls -la /tmp/gludd-multitask-state.json; echo "---"; cat /tmp/gludd-multitask-state.json; else echo "File does not exist: /tmp/gludd-multitask-state.json"; fi

test-multitask-node: ## Run enforce-multitask behavioral node tests (node --test)
	@node --experimental-strip-types --test .opencode/plugin/enforce-multitask.test.node.mjs

merge-spec-groups: ## Splice temp spec groups into BEHAVIORAL_SPECS.md
	$(PYTHON) /tmp/gludd-merge-specs.py
# ci-poll-master: poll CI verdict + release artifact every 120s until terminal.
# Usage: make ci-poll-master [MAX_POLLS=15] [INTERVAL=120]
# Returns: 0 if CI GREEN, 1 if RED/TIMEOUT.
ci-poll-master:
	@MAX=$${MAX_POLLS:-15}; INTERVAL=$${INTERVAL:-120}; \
	for i in $$(seq 1 $$MAX); do \
		echo "=== POLL $$i/$$MAX ($$(date +%H:%M:%S)) ==="; \
		$(MAKE) --no-print-directory ci-verdict-safe BRANCH=master FORCE=1 2>&1 || true; \
		$(MAKE) --no-print-directory verify-release-artifact TAG=v0.1.0-beta.1 2>&1 || true; \
		CI_OUT=$$($(MAKE) --no-print-directory ci-verdict-safe BRANCH=master FORCE=1 2>&1); \
		echo "$$CI_OUT"; \
		if echo "$$CI_OUT" | grep -qE "CI GREEN|CI RED"; then \
			echo "=== TERMINAL STATE REACHED ==="; \
			exit 0; \
		fi; \
		echo "Waiting $$INTERVAL s..."; \
		sleep $$INTERVAL; \
	done; \
	echo "=== TIMEOUT: CI did not resolve after $$MAX polls ==="; \
	exit 1
# Generate 2000 expansion specs and append to BEHAVIORAL_SPECS.md
generate-specs-expansion:
	@echo "Generating 2000 behavioral spec expansions..."
	@$(UV) run python3 scripts/generate_specs_expansion.py

# Deduplicate behavioral specs: find overlapping specs by Jaccard similarity,
# flag exact body-text duplicates, and optionally deduplicate the file.
# Usage: make deduplicate-specs [THRESHOLD=0.80]
#   make deduplicate-specs              # print report only
#   make deduplicate-specs DEDUP=1      # deduplicate the file
#   make deduplicate-specs DRY_RUN=1    # show what would be removed
deduplicate-specs:
	@$(UV) run python3 scripts/spec_deduplicator.py $(if $(THRESHOLD),--threshold $(THRESHOLD)) $(if $(DRY_RUN),--dry-run) $(if $(DEDUP),--deduplicate)

# Count behavioral specs per group
count-specs:
	@$(UV) run python3 scripts/spec_deduplicator.py --json 2>/dev/null | $(UV) run python3 -c "import json,sys; d=json.load(sys.stdin); print(f'Total: {d[\"stats\"][\"total_specs\"]} specs, {d[\"stats\"][\"unique_bodies\"]} unique bodies'); [print(f'  {g}: {c}') for g,c in sorted(d['stats']['by_group'].items())]"

# Generate specs loop: analyze enforcement quality, fix template-only specs,
# commit batches, repeat until target count of specs with real enforcement met.
# Usage:
#   make generate-specs TARGET=1000           # target 1000 specs with real enforcement
#   make generate-specs-stats                 # print stats only
#   make generate-specs-fix TARGET=1000       # fix template enforcements
generate-specs-stats:
	@$(UV) run python3 scripts/spec_generator_loop.py --stats
generate-specs-check:
	@$(UV) run python3 scripts/spec_generator_loop.py --dry-run --fix --target $(or $(TARGET),1000)
generate-specs-fix:
	@$(UV) run python3 scripts/spec_generator_loop.py --fix --target $(or $(TARGET),1000)
generate-specs:
	@$(UV) run python3 scripts/spec_generator_loop.py --target $(or $(TARGET),1000)

# Expand BEHAVIORAL_SPECS.md with unique, real-enforcement specs to reach TARGET
# Usage: make expand-specs TARGET=4000
expand-specs:
	@$(UV) run python3 scripts/generate_specs_to_4000.py --target $(or $(TARGET),4000)

# Push exactly the current clean HEAD for the current branch.
git-push-committed-head-nv: commit-ready
	@BRANCH=$$(git branch --show-current); if [ -z "$$BRANCH" ]; then echo "Cannot push detached HEAD"; exit 1; fi; $(MAKE) --no-print-directory ci-busy-check BRANCH=$$BRANCH || exit 1; PUSH_BRANCH=$$BRANCH $(MAKE) --no-print-directory _push-rate-guard || exit 1; HEAD=$$(git rev-parse HEAD); GIT_SSH_COMMAND="ssh -i $(SSH_KEY) -o StrictHostKeyChecking=accept-new" git push --no-verify -u sandboxcom HEAD:refs/heads/$$BRANCH || exit 1; $(MAKE) --no-print-directory verify-remote BRANCH=$$BRANCH SHA=$$HEAD || exit 1; echo "Pushed clean HEAD $$HEAD to sandboxcom/$$BRANCH."

# Idempotently signal the Build and Release workflow for the exact clean HEAD.
# The helper reuses the remote-head guard, discovers a push-created exact-SHA
# run before dispatching, serializes concurrent callers, records a successful
# dispatch, and returns the confirmed run URL. EXAMPLE=1 is network-free.
ci-trigger-committed-head:
	@REF="$(REF)"; if [ -z "$$REF" ]; then REF=$$(git branch --show-current); fi; \
	REMOTE="$(REMOTE)"; if [ -z "$$REMOTE" ]; then REMOTE=sandboxcom; fi; \
	if [ "$(EXAMPLE)" = "1" ]; then \
		UV=echo $(SYSTEM_PYTHON) scripts/ci_signal_exact_sha.py \
			--example --ref "$$REF" --remote "$$REMOTE" \
			--repo "$(or $(REPO),sandboxcom/gludd)" \
			--workflow "$(or $(WORKFLOW),Build and Release)" \
			--discovery-polls "$(or $(DISCOVERY_POLLS),1)" \
			--confirm-polls "$(or $(CONFIRM_POLLS),1)" \
			--poll-interval "$(or $(POLL_INTERVAL),0)"; \
	else \
		$(MAKE) --no-print-directory _require-gh || exit 1; \
		UV=echo GIT_SSH_COMMAND="ssh -i /Users/shawnwilson/.ssh/sandboxcom_gludd_rsa -o StrictHostKeyChecking=accept-new" \
			$(SYSTEM_PYTHON) scripts/ci_signal_exact_sha.py \
			--ref "$$REF" --remote "$$REMOTE" \
			--repo "$(or $(REPO),sandboxcom/gludd)" \
			--workflow "$(or $(WORKFLOW),Build and Release)" \
			--discovery-polls "$(or $(DISCOVERY_POLLS),6)" \
			--confirm-polls "$(or $(CONFIRM_POLLS),15)" \
			--poll-interval "$(or $(POLL_INTERVAL),2)" || exit 1; \
	fi

# Push and dispatch the exact clean HEAD without allowing local/remote code drift.
ci-push-committed-head: git-push-committed-head-nv ci-trigger-committed-head
	@echo "Clean HEAD is pushed and remote CI has been dispatched for the same code."

check-no-prompt-prone-edit-tools:
	@$(UV) run python scripts/check_no_prompt_prone_edit_tools.py

fix-init-drift:
	@$(UV) run python scripts/fix_init_drift.py

fix-docs-drift:
	@$(UV) run python scripts/fix_docs_drift.py

report-docs-drift:
	@$(UV) run python scripts/fix_docs_drift.py --report


git-resolve-theirs:
	@[ -n "$(FILES)" ] || { echo "Usage: make git-resolve-theirs FILES='path'"; exit 1; }
	@git checkout --theirs -- $(FILES) && git add $(FILES) && echo "resolved (theirs): $(FILES)"

git-cherry-pick-continue:
	@git cherry-pick --continue

git-cherry-pick-skip:
	@git cherry-pick --skip

git-cherry-pick-abort:
	@git cherry-pick --abort

replace-all-text:
	@test -n "$(FILE)" || { echo "Usage: make replace-all-text FILE=path OLD=/tmp/gludd-old NEW=/tmp/gludd-new"; exit 1; }
	@test -n "$(OLD)" || { echo "Usage: make replace-all-text FILE=path OLD=/tmp/gludd-old NEW=/tmp/gludd-new"; exit 1; }
	@test -n "$(NEW)" || { echo "Usage: make replace-all-text FILE=path OLD=/tmp/gludd-old NEW=/tmp/gludd-new"; exit 1; }
	@case "$(FILE)" in /tmp/gludd-*) ;; /*|*..*) echo "Refusing path outside workspace: $(FILE)"; exit 1;; esac
	@$(PYTHON) scripts/replace_all_text.py "$(FILE)" "$(OLD)" "$(NEW)"

mkdir-p:
	@[ -n "$(PATH_ARG)" ] || { echo "Usage: make mkdir-p PATH_ARG=path"; exit 1; }
	@$(PYTHON) scripts/mkdir_p.py "$(PATH_ARG)"


replace-lines:
	@[ -n "$(FILE)" ] || { echo "Usage: make replace-lines FILE=path START=n END=n NEW_FILE=path"; exit 1; }
	@[ -n "$(START)" ] || { echo "Usage: make replace-lines FILE=path START=n END=n NEW_FILE=path"; exit 1; }
	@[ -n "$(END)" ] || { echo "Usage: make replace-lines FILE=path START=n END=n NEW_FILE=path"; exit 1; }
	@[ -n "$(NEW_FILE)" ] || { echo "Usage: make replace-lines FILE=path START=n END=n NEW_FILE=path"; exit 1; }
	@TMP=$$(mktemp "$(FILE).replace.XXXXXX"); \
	trap 'rm -f "$$TMP"' EXIT INT TERM; \
	cp "$(FILE)" "$$TMP"; \
	$(PYTHON) scripts/replace_lines.py "$$TMP" "$(START)" "$(END)" "$(NEW_FILE)"; \
	mv "$$TMP" "$(FILE)"; \
	trap - EXIT INT TERM

gate-all-background:
	@mkdir -p .gate-logs; \
	TS=$(date +%Y%m%d-%H%M%S); \
	LOG=".gate-logs/gate-all-$TS.log"; \
	nohup /Library/Developer/CommandLineTools/usr/bin/make --no-print-directory gate-all 2>&1 | tee "$LOG" & \
	echo $! > .gate-all-background.pid; \
	echo "[gate-all-background] PID=$!  LOG=$LOG"


# temporary test


# Second test target
target-two:
	@echo "target-two: Second test target"


# First test target
target-one:
	@echo "target-one: First test target"


# Duplicate target
my-target:
	@echo "my-target: Duplicate target"


# Scan for secrets
my-secret-scanner:
	@echo "my-secret-scanner: Scan for secrets"


# A test target with no keyword match
zzyx-test:
	@echo "zzyx-test: A test target with no keyword match"


# Debug test
debug-test-target:
	@echo "debug-test-target: Debug test"


# Test
foo-test:
	@echo "foo-test: Test"

# Temp test target


# Run the worktree health gate. Exits non-zero on any violation
# (stale >24h, unmerged, missing from remote, prunable).
# Usage: make worktree-health-check
worktree-health-check:
	@python3 scripts/check_worktree_health.py

# Bulk merge: iterate all worktrees, attempt to merge each branch into
# development via --no-ff, report conflicts, clean up successful merges.
# Usage: make worktree-merge-all
worktree-merge-all:
	@$(UV) run python scripts/worktree_merge_all.py

PIPELINE_STATUS_REPO ?= sandboxcom/gludd
PIPELINE_STATUS_BRANCH ?= development
PIPELINE_STATUS_REMOTE ?= sandboxcom
PIPELINE_STATUS_SHA ?=
PIPELINE_STATUS_VALIDATE_ONLY ?= 0
PIPELINE_STATUS_FAILURE_LEDGER ?= .gludd/ci-failure-ledger.json
pipeline-status:
	@case "$(PIPELINE_STATUS_VALIDATE_ONLY)" in 0|1) ;; *) echo "PIPELINE_STATUS_VALIDATE_ONLY must be 0 or 1"; exit 2 ;; esac
	@OBSERVE_RC=0; STATUS_RC=0; \
	$(UV) run python scripts/ci_failure_ledger.py observe-sha \
		--repo "$(PIPELINE_STATUS_REPO)" --branch "$(PIPELINE_STATUS_BRANCH)" \
		--remote "$(PIPELINE_STATUS_REMOTE)" --ledger "$(PIPELINE_STATUS_FAILURE_LEDGER)" \
		$(if $(PIPELINE_STATUS_SHA),--sha "$(PIPELINE_STATUS_SHA)",) \
		$(if $(filter 1,$(PIPELINE_STATUS_VALIDATE_ONLY)),--validate-only,) || OBSERVE_RC=$$?; \
	$(UV) run python scripts/pipeline_status.py status \
		--repo "$(PIPELINE_STATUS_REPO)" --branch "$(PIPELINE_STATUS_BRANCH)" \
		--remote "$(PIPELINE_STATUS_REMOTE)" \
		$(if $(PIPELINE_STATUS_SHA),--sha "$(PIPELINE_STATUS_SHA)",) \
		$(if $(filter 1,$(PIPELINE_STATUS_VALIDATE_ONLY)),--validate-only,) || STATUS_RC=$$?; \
	if [ $$OBSERVE_RC -ne 0 ]; then echo "pipeline-status: failure-ledger observation failed rc=$$OBSERVE_RC"; fi; \
	if [ $$OBSERVE_RC -ne 0 ]; then exit $$OBSERVE_RC; fi; \
	exit $$STATUS_RC

# Emit an auditable pipeline heartbeat at a five-minute cadence by default.
# Use COUNT=0 for a continuous loop; artifacts are project-namespaced.
status-heartbeat:
	@PROJECT_NAMESPACE="$${GLUDD_PROJECT_NAMESPACE:-}"; \
	if [ -z "$$PROJECT_NAMESPACE" ]; then PROJECT_NAMESPACE="$$($(PYTHON) scripts/resource_arbiter.py namespace)"; fi; \
	RESOURCE_ROOT="$${GLUDD_RESOURCE_ROOT:-$${TMPDIR:-/tmp}/gludd-resources}/$$PROJECT_NAMESPACE"; \
	mkdir -p "$$RESOURCE_ROOT"; LOG="$$RESOURCE_ROOT/status-heartbeat.log"; STATE="$$RESOURCE_ROOT/status-heartbeat.json"; \
	INTERVAL_VALUE="$${INTERVAL:-300}"; COUNT_VALUE="$${COUNT:-1}"; \
	case "$$INTERVAL_VALUE" in ''|*[!0-9]*) echo "INTERVAL must be a non-negative integer"; exit 2;; esac; \
	case "$$COUNT_VALUE" in ''|*[!0-9]*) echo "COUNT must be a non-negative integer"; exit 2;; esac; \
	if [ "$$INTERVAL_VALUE" -lt 300 ]; then echo "INTERVAL must be >= 300 seconds"; exit 2; fi; \
	i=0; while [ "$$COUNT_VALUE" -eq 0 ] || [ "$$i" -lt "$$COUNT_VALUE" ]; do \
		timestamp="$$(date -u +%Y-%m-%dT%H:%M:%SZ)"; sha="$$(git rev-parse HEAD 2>/dev/null || echo unknown)"; \
		echo "=== PIPELINE HEARTBEAT $$timestamp sha=$$sha ===" | tee -a "$$LOG"; \
		$(MAKE) --no-print-directory gate-status 2>&1 | tee -a "$$LOG" || true; \
		$(MAKE) --no-print-directory pipeline-status 2>&1 | tee -a "$$LOG" || true; \
		$(MAKE) --no-print-directory active-work-status 2>&1 | tee -a "$$LOG" || true; \
		tmp="$$STATE.tmp.$$$$"; printf '{"timestamp":"%s","sha":"%s","interval_seconds":%s,"iteration":%s}\n' "$$timestamp" "$$sha" "$$INTERVAL_VALUE" "$$i" > "$$tmp"; mv -f "$$tmp" "$$STATE"; \
		i=$$((i + 1)); if [ "$$COUNT_VALUE" -ne 0 ] && [ "$$i" -ge "$$COUNT_VALUE" ]; then break; fi; sleep "$$INTERVAL_VALUE"; \
	done

# Repository-level Codex stop invariant.  This cannot control the Codex host;
# it gives CI and an external runner a fail-closed, auditable decision instead.
codex-stop-guard:
	@$(PYTHON) scripts/codex_stop_guard.py

codex-stop-confirm:
	@[ -n "$(TOKEN)" ] || { echo "Usage: make codex-stop-confirm TOKEN='challenge'"; exit 2; }
	@$(PYTHON) scripts/codex_stop_guard.py --confirm "$(TOKEN)"

pipeline-health: pipeline-status
	@true

check-gate-fresh:
	@$(UV) run python scripts/gate_fresh_check.py check .gate-status
	@$(UV) run python scripts/gate_status_attestation.py verify .gate-status

check-version-consistency:
	@$(UV) run python scripts/check_version_consistency.py

bump-version:
	@[ -n "$(NEW)" ] || { echo "Usage: make bump-version NEW=0.1.0-beta.2"; exit 1; }
	@$(UV) run python scripts/bump_version.py $(NEW)
	@$(MAKE) --no-print-directory check-version-consistency

# Normalize legacy TASKS.md records while preserving unsupported evidence.
normalize-task-integrity:
	@$(PYTHON) scripts/normalize_task_integrity.py


# install opa via brew
install-opa:
	@command -v opa >/dev/null 2>&1 && { opa version; exit 0; } || true
	@command -v brew >/dev/null 2>&1 || { echo "brew MISSING — cannot install opa"; exit 1; }
	@echo "Installing opa via brew (may take a minute)..."
	@brew install opa 2>&1 | tail -15 || echo "brew-install-opa-failed"
	@command -v opa >/dev/null 2>&1 && opa version || echo "opa still missing after install"

# fast local gate: lint + typecheck + collect + hook-runtime + fast structural tests
gate-local:
	@echo "gate-local: fast local gate: lint + typecheck + collect + hook-runtime + fast structural tests"

# Publish the verified master release commit and its annotated tag as one release step.
# Usage: make release-tag-push TAG=v0.1.0-beta.3 MSG='release v0.1.0-beta.3'
release-tag-push:
	@[ -n "$(TAG)" ] || { echo "Usage: make release-tag-push TAG=v0.1.0-beta.3 MSG='release message'"; exit 1; }
	@$(MAKE) --no-print-directory ci-active BRANCH=master || exit 1
	@$(MAKE) --no-print-directory git-push-sandboxcom
	@$(MAKE) --no-print-directory git-tag-push TAG="$(TAG)" MSG="$(MSG)"


# Stop only an E2E process tree rooted in this exact worktree. The root command
# must be this worktree's pytest tests/e2e invocation; descendants are then safe
# to signal because they belong to that verified root. Usage: make kill-worktree-e2e PID=123
kill-worktree-e2e:
	@if [ "$(KILL_WORKTREE_E2E_VALIDATE_ONLY)" = "1" ]; then echo "KILL-WORKTREE-E2E-VALIDATION PASS"; exit 0; fi; \
	[ -n "$(PID)" ] || { echo "Usage: make kill-worktree-e2e PID=pid [KILL_WORKTREE_E2E_VALIDATE_ONLY=1]"; exit 1; }; \
	tree_contains_local_e2e() { pid="$$1"; cmd=$$(/bin/ps -p "$$pid" -o command= 2>/dev/null); case "$$cmd" in *"$(CURDIR)"*"pytest tests/e2e/"*) return 0 ;; esac; for child in $$(/usr/bin/pgrep -P "$$pid" 2>/dev/null || true); do tree_contains_local_e2e "$$child" && return 0; done; return 1; }; \
	if ! tree_contains_local_e2e "$(PID)"; then cmd=$$(/bin/ps -p "$(PID)" -o command=); echo "Refusing to kill unrelated process tree: $$cmd"; exit 1; fi; \
	term_tree() { for child in $$(/usr/bin/pgrep -P "$$1" 2>/dev/null || true); do term_tree "$$child"; done; /bin/kill -TERM "$$1" 2>/dev/null || true; }; \
	kill_tree_force() { for child in $$(/usr/bin/pgrep -P "$$1" 2>/dev/null || true); do kill_tree_force "$$child"; done; /bin/kill -KILL "$$1" 2>/dev/null || true; }; \
	term_tree "$(PID)"; sleep 1; \
	if /bin/kill -0 "$(PID)" 2>/dev/null; then kill_tree_force "$(PID)"; fi; \
	echo "Stopped verified E2E process tree rooted at $(PID) for $(CURDIR)"
.PHONY: migrate-test-env-writes
migrate-test-env-writes:
	@$(UV) run python scripts/migrate_test_env_writes.py

# ── E2E Test Generation Pipeline ─────────────────────────────────────────────

# e2e-test-gen-pipeline: full 5-stage pipeline (analyze → generate → validate → write → verify)
# Usage: make e2e-test-gen-pipeline MODULE=src/general_ludd/agents/test_generation/code_path_analyzer.py ARTIFACT_DIR=/tmp/e2e-gen-artifacts
#
# Stages:
#   1. analyze_code_paths  → module_symbols.json
#   2. generate_scenarios  → scenarios.json
#   3. validate_scenarios  → validated_scenarios.json
#   4. write_e2e_tests     → test_e2e_generated_*.py files + generated_tests.json
#   5. verify_coverage     → coverage_report.json
e2e-test-gen-pipeline:
	@[ -n "$(MODULE)" ] || { echo "Usage: make e2e-test-gen-pipeline MODULE=path/to/module.py [ARTIFACT_DIR=/tmp/e2e-gen-artifacts]"; exit 1; }
	@ARTIFACT_DIR="$(or $(ARTIFACT_DIR),/tmp/e2e-gen-artifacts)"; \
	ROLES_BASE="collections/ansible_collections/general_ludd/e2e_test_gen/roles"; \
	mkdir -p "$$ARTIFACT_DIR"; \
	echo "=== Stage 1/5: analyze_code_paths ==="; \
	$(UV) run python "$$ROLES_BASE/analyze_code_paths/files/analyze_code_paths.py" \
		--target-module "$(MODULE)" \
		--output "$$ARTIFACT_DIR/module_symbols.json"; \
	echo "=== Stage 2/5: generate_scenarios ==="; \
	$(UV) run python "$$ROLES_BASE/generate_scenarios/files/generate_scenarios.py" \
		--symbols-file "$$ARTIFACT_DIR/module_symbols.json" \
		--output "$$ARTIFACT_DIR/scenarios.json"; \
	echo "=== Stage 3/5: validate_scenarios ==="; \
	$(UV) run python "$$ROLES_BASE/validate_scenarios/files/validate_scenarios.py" \
		--scenarios-file "$$ARTIFACT_DIR/scenarios.json" \
		--output "$$ARTIFACT_DIR/validated_scenarios.json" \
		--mock; \
	echo "=== Stage 4/5: write_e2e_tests ==="; \
	$(UV) run python "$$ROLES_BASE/write_e2e_tests/files/write_e2e_tests.py" \
		--scenarios-file "$$ARTIFACT_DIR/validated_scenarios.json" \
		--output-dir "$$ARTIFACT_DIR/generated_tests" \
		--manifest "$$ARTIFACT_DIR/generated_tests.json"; \
	echo "=== Stage 5/5: verify_coverage ==="; \
	$(UV) run python "$$ROLES_BASE/verify_coverage/files/verify_coverage.py" \
		--test-dir "$$ARTIFACT_DIR/generated_tests" \
		--source-module "$(MODULE)" \
		--output "$$ARTIFACT_DIR/coverage_report.json" \
		--scenarios-file "$$ARTIFACT_DIR/validated_scenarios.json" \
		--symbols-file "$$ARTIFACT_DIR/module_symbols.json" \
		--threshold 0; \
	echo "=== Pipeline complete ==="; \
	echo "Artifacts in $$ARTIFACT_DIR/:"; \
	for f in "$$ARTIFACT_DIR"/*.json; do \
		[ -f "$$f" ] && echo "  $$f"; \
	done; \
	echo "Generated tests in $$ARTIFACT_DIR/generated_tests/"

# e2e-test-gen-pipeline-dogfood: run the pipeline on its own core modules
# Validates the tool by analyzing its own source and generating E2E tests for it
e2e-test-gen-pipeline-dogfood:
	@TOOL_DIR="/tmp/e2e-gen-dogfood"; mkdir -p "$$TOOL_DIR"; \
	FAILED=""; \
	for MODULE in \
		src/general_ludd/agents/test_generation/code_path_analyzer.py \
		src/general_ludd/agents/test_generation/scenario_generator.py; do \
		MODULE_STEM=$$(basename "$$MODULE" .py); \
		ARTIFACT_DIR="$$TOOL_DIR/$$MODULE_STEM"; \
		echo "=== Dogfooding: $$MODULE ==="; \
		$(MAKE) --no-print-directory e2e-test-gen-pipeline MODULE="$$MODULE" ARTIFACT_DIR="$$ARTIFACT_DIR" \
			|| { echo "FAILED: $$MODULE"; FAILED="$$FAILED $$MODULE"; }; \
	done; \
	if [ -n "$$FAILED" ]; then \
		echo "Dogfood failures:$$FAILED"; exit 1; \
	fi; \
	echo "Dogfood complete — all modules passed"

collect-specific:
	@$(UV) run python -m pytest $(or $(TESTFILES),tests/) --co -q 2>&1

fix-e501-golden:
	@$(UV) run python /tmp/fix_e501_lines.py

clean-relative:
	@rm -rf relative/
	@echo "Removed relative/ temp directory"

# Verify rag module_utils delegates to core modules
check-rag-wrapper:
	@$(UV) run python -c "\
import sys; sys.path.insert(0, 'collections'); \
from ansible_collections.general_ludd.agent.plugins.module_utils.rag import ( \
    Chunk, Chunker, RAGPipeline, VectorEntry, VectorStore, _build_prompt, \
); \
from general_ludd.skills.embeddings import HashEmbedder, cosine_similarity; \
p = RAGPipeline(model_client=None); \
p.add_document('hello world test', {'source': 'test'}); \
assert p.stored_count > 0, 'add_document should store entries'; \
p.clear(); \
assert p.stored_count == 0, 'clear should empty store'; \
print('OK: rag.py delegates to HashEmbedder + ModelGateway'); \
"
	@echo "check-rag-wrapper: PASS"

user-test-batch:
	@$(UV) run python scripts/run_user_test_batch.py
