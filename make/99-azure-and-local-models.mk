# --- Azure Event Guard (Azure Activity Log smoke-test guard) ---
# Wraps scripts/azure_event_guard.sh: monitors Azure Activity Log to catch
# expensive GPU types, duplicate resource names, and wrong-subscription usage.
# --once: one-shot check (exit 0=clean, 1=violation, 2=auth error)
# --watch: poll every 60s; exit 1 on first violation
azure-event-guard-start:
	@echo "Starting Azure Event Guard (watch mode)..."
	@nohup bash scripts/azure_event_guard.sh --watch > .gate-logs/azure-event-guard.log 2>&1 & echo $$! > .gate-logs/azure-event-guard.pid; echo "azure-event-guard PID=$$(cat .gate-logs/azure-event-guard.pid)"

azure-event-guard-stop:
	@if [ -f .gate-logs/azure-event-guard.pid ]; then \
		kill $$(cat .gate-logs/azure-event-guard.pid) 2>/dev/null || true; \
		rm -f .gate-logs/azure-event-guard.pid; \
		echo "Azure Event Guard stopped"; \
	else \
		echo "No Azure Event Guard running"; \
	fi

azure-event-guard-check:
	@bash scripts/azure_event_guard.sh --once

azure-event-guard-status:
	@echo "=== Azure Event Guard status ==="
	@if [ -f .gate-logs/azure-event-guard.pid ]; then \
		echo "PID: $$(cat .gate-logs/azure-event-guard.pid)"; \
		ps -p $$(cat .gate-logs/azure-event-guard.pid) > /dev/null 2>&1 && echo "Status: running" || echo "Status: stopped"; \
	else \
		echo "No PID file — Azure Event Guard not started"; \
	fi
	@echo "--- Last 15 log lines ---"
	@tail -15 .gate-logs/azure-event-guard.log 2>/dev/null || echo "No log yet"

.PHONY: e2e-test-gen-pipeline e2e-test-gen-pipeline-dogfood collect-specific fix-e501-golden clean-relative check-rag-wrapper user-test-batch azure-event-guard-start azure-event-guard-stop azure-event-guard-check azure-event-guard-status check-e2e-small-model-prereq check-deepseek-key check-openrouter-key e2e-download-small-model download-1.5b-model download-phi3-mini test-phi3-mini-game-gen download-deepseek-1.3b benchmark-codegen-quality

check-e2e-small-model-prereq:
	@echo "=== E2E small model pipeline prerequisites ==="
	@$(UV) run python -c "import huggingface_hub; print('huggingface_hub:', huggingface_hub.__version__)" && echo "  huggingface_hub: OK" || echo "  huggingface_hub: MISSING"
	@$(UV) run python -c "import llama_cpp; print('llama_cpp:', llama_cpp.__version__); print('llama_cpp.server:', type(llama_cpp))" && echo "  llama_cpp: OK" || echo "  llama_cpp: MISSING"
	@if [ -f external/llamacpp/build/bin/llama-quantize ] && [ -x external/llamacpp/build/bin/llama-quantize ]; then echo "  llama-quantize (bundled): external/llamacpp/build/bin/llama-quantize OK"; else echo "  llama-quantize (bundled): MISSING"; fi
	@which llama-quantize >/dev/null 2>&1 && echo "  llama-quantize (PATH): $(shell which llama-quantize) OK" || echo "  llama-quantize (PATH): not on PATH"

compute-model-hashes:
	@$(UV) run python scripts/compute_model_hashes.py

e2e-download-small-model:
	@mkdir -p /tmp/gludd-qwen-e2e-model
	@echo "=== Downloading Qwen2.5-0.5B GGUF to /tmp/gludd-qwen-e2e-model/ ==="
	@$(UV) run python scripts/e2e_download_small_model.py
	@echo "=== Model cached at /tmp/gludd-qwen-e2e-model/ ==="
	@ls -lh /tmp/gludd-qwen-e2e-model/

E2E_SMALL_MODEL_CLEAN_VALIDATE_ONLY ?= 1

.PHONY: clean-e2e-small-model
clean-e2e-small-model:
	@if [ "$(E2E_SMALL_MODEL_CLEAN_VALIDATE_ONLY)" != "0" ] && [ "$(E2E_SMALL_MODEL_CLEAN_VALIDATE_ONLY)" != "1" ]; then \
		echo "ERROR: E2E_SMALL_MODEL_CLEAN_VALIDATE_ONLY must be 0 or 1"; exit 2; \
	fi
	@if [ "$(E2E_SMALL_MODEL_CLEAN_VALIDATE_ONLY)" = "1" ]; then \
		echo "Would remove only /tmp/gludd-qwen-e2e-model"; \
	else \
		rm -rf -- /tmp/gludd-qwen-e2e-model; \
		echo "Removed /tmp/gludd-qwen-e2e-model (recover with make e2e-download-small-model)"; \
	fi

download-1.5b-model:
	@echo "=== Downloading Qwen2.5-1.5B-Instruct-Q4_K_M GGUF (~1.0 GB) ==="
	@$(UV) run python scripts/download_1_5b_model.py
	@echo "=== Model cached at /tmp/gludd-qwen-1.5b-model/ ==="
	@ls -lhS /tmp/gludd-qwen-1.5b-model/

download-phi3-mini:
	@echo "=== Downloading Phi-3.1-mini-4k-instruct GGUF (~2.2 GB) ==="
	@$(UV) run python scripts/download_phi3_mini.py
	@echo "=== Model cached at /tmp/gludd-phi3-mini-model/ ==="
	@ls -lhS /tmp/gludd-phi3-mini-model/

test-phi3-mini-game-gen: download-phi3-mini
	@echo "=== Phi-3.1-mini-4k game gen quality + speed benchmark ==="
	@$(UV) run python scripts/download_phi3_mini.py

.PHONY: verify-local-model-quality
verify-local-model-quality:
	@$(UV) run python scripts/verify_local_model_quality.py

.PHONY: benchmark-local-model
benchmark-local-model:
	@$(UV) run python scripts/benchmark_local_model.py

.PHONY: benchmark-models
benchmark-models:
	@$(UV) run python scripts/benchmark_models.py

.PHONY: run-game-gen-1.5b
run-game-gen-1.5b:
	@$(UV) run python scripts/run_game_gen_1_5b.py

LOCAL_MODEL_INFERENCE_MODEL_PATH ?= /tmp/gludd-qwen-e2e-model/Qwen2.5-0.5B-Instruct-Q4_K_M.gguf
LOCAL_MODEL_INFERENCE_VALIDATE_ONLY ?= 0

.PHONY: test-local-model-inference
test-local-model-inference:
	@echo "=== Local model inference test ==="
	@if [ "$(LOCAL_MODEL_INFERENCE_VALIDATE_ONLY)" != "0" ] && [ "$(LOCAL_MODEL_INFERENCE_VALIDATE_ONLY)" != "1" ]; then \
		echo "ERROR: LOCAL_MODEL_INFERENCE_VALIDATE_ONLY must be 0 or 1"; exit 2; \
	fi
	@if [ "$(LOCAL_MODEL_INFERENCE_VALIDATE_ONLY)" = "1" ]; then \
		$(MAKE) --no-print-directory sync DEPENDENCY_PROFILE_SET=local-inference DEPENDENCY_PROFILE_ENVIRONMENT=.venv DEPENDENCY_PROFILE_PYTHON= DEPENDENCY_PROFILE_VALIDATE_ONLY=1; \
		echo "LOCAL_MODEL_INFERENCE_CONFIG_OK profile_set=local-inference model_path=$(LOCAL_MODEL_INFERENCE_MODEL_PATH)"; \
	else \
		if [ ! -r "$(LOCAL_MODEL_INFERENCE_MODEL_PATH)" ]; then \
			echo "ERROR: GGUF artifact not readable: $(LOCAL_MODEL_INFERENCE_MODEL_PATH)"; \
			echo "Run make e2e-download-small-model or set LOCAL_MODEL_INFERENCE_MODEL_PATH explicitly."; \
			exit 2; \
		fi; \
		$(MAKE) --no-print-directory sync DEPENDENCY_PROFILE_SET=local-inference DEPENDENCY_PROFILE_ENVIRONMENT=.venv DEPENDENCY_PROFILE_PYTHON= DEPENDENCY_PROFILE_VALIDATE_ONLY=0; \
		UV_NO_PROGRESS=1 $(UV) run --no-sync python scripts/local_model_inference_smoke.py \
			--model-path "$(LOCAL_MODEL_INFERENCE_MODEL_PATH)"; \
	fi

download-deepseek-1.3b:
	@echo "=== Downloading DeepSeek-Coder-1.3B-Instruct-Q4_K_M GGUF (~0.8 GB) ==="
	@$(UV) run python scripts/download_deepseek_1_3b.py
	@echo "=== Model cached at /tmp/gludd-deepseek-1.3b-model/ ==="
	@ls -lhS /tmp/gludd-deepseek-1.3b-model/

benchmark-codegen-quality:
	@echo "=== Code generation quality: DeepSeek-Coder-1.3B vs Qwen2.5-1.5B ==="
	@$(UV) run python scripts/benchmark_codegen_quality.py

deepseek-key-dir := $(HOME)/.config/gludd/keys
deepseek-key-file := $(deepseek-key-dir)/deepseek.key
openrouter-key-file := $(deepseek-key-dir)/openrouter.key

check-deepseek-key:
	@if [ -n "$$DEEPSEEK_API_KEY" ]; then \
		echo "DEEPSEEK_API_KEY: env var OK"; exit 0; \
	elif [ -f "$(deepseek-key-file)" ]; then \
		echo "DEEPSEEK_API_KEY: key file OK ($(deepseek-key-file))"; exit 0; \
	else \
		echo "DEEPSEEK_API_KEY: MISSING (set DEEPSEEK_API_KEY env var or create $(deepseek-key-file))"; exit 1; \
	fi

check-openrouter-key:
	@if [ -n "$$OPENROUTER_API_KEY" ]; then \
		echo "OPENROUTER_API_KEY: env var OK"; exit 0; \
	elif [ -f "$(openrouter-key-file)" ]; then \
		echo "OPENROUTER_API_KEY: key file OK ($(openrouter-key-file))"; exit 0; \
	else \
		echo "OPENROUTER_API_KEY: MISSING (set OPENROUTER_API_KEY env var or create $(openrouter-key-file))"; exit 1; \
	fi

diag-opencode-e2e-2test:
	@export GLUDD_MAINTHREAD_STREAK_ENFORCE=0 GLUDD_FLOOR_ENFORCE=0 && bash /tmp/opencode-e2e-diag2.sh

diag-opencode-raw-json-pure-no-enforce:
	@echo "=== opencode --pure (enforcement disabled) ==="
	@rm -f /tmp/gludd-raw-json-pure-noenf-*.log
	@cd /Users/shawnwilson/gludd/tests/opencode_e2e/_test_project && GLUDD_MAINTHREAD_STREAK_ENFORCE=0 GLUDD_FLOOR_ENFORCE=0 GLUDD_SESSION_START_ENFORCE=0 GLUDD_MULTITASK_FLOOR_ENFORCE=0 printf 'Say hello and then exit.\n' | opencode run --format json --auto --pure --log-level ERROR --model deepseek/deepseek-v4-pro 2>/tmp/gludd-raw-json-pure-noenf-stderr.log > /tmp/gludd-raw-json-pure-noenf-stdout.log
	@echo "EXIT: $$?"
	@echo "--- STDOUT first 100 lines ---"
	@head -100 /tmp/gludd-raw-json-pure-noenf-stdout.log 2>/dev/null || true
	@echo "--- STDERR ---"
	@head -20 /tmp/gludd-raw-json-pure-noenf-stderr.log 2>/dev/null || true

.PHONY: compare-models
compare-models:
	@echo "=== Multi-model comparison benchmark ==="
	@$(UV) run python scripts/compare_models.py

# --- New Targets (auto-categorized add-target) ---
# Promote the exact green development commit from the canonical main checkout.
# Validation mode proves topology and release policy without network or ref writes.
# Real mode revalidates exact-SHA evidence, fast-forwards master in the main
# checkout, then delegates tag publication and artifact verification to release-cut.
# Usage: make release-promote TAG=v0.1.0-beta.N MSG=release-notes RELEASE_PROMOTE_VALIDATE_ONLY=0|1 REVIEWED_HEAD_INTEGRATION_RECEIPT=path
release-promote:
	@[ -n "$(TAG)" ] || { echo "Usage: make release-promote TAG=v0.1.0-beta.N [MSG=release-notes] [RELEASE_PROMOTE_VALIDATE_ONLY=0|1] [REVIEWED_HEAD_INTEGRATION_RECEIPT=path]"; exit 2; }
	@case "$(RELEASE_PROMOTE_VALIDATE_ONLY)" in 0|1|"") ;; *) echo "ERROR: RELEASE_PROMOTE_VALIDATE_ONLY must be 0 or 1"; exit 2;; esac
	@MAIN_PATH='/Users/shawnwilson/gludd'; \
	CURRENT_SHA="$$(git rev-parse --verify HEAD^{commit})" || { echo "ERROR: current HEAD does not resolve"; exit 2; }; \
	DEV_SHA="$$(git rev-parse --verify development^{commit})" || { echo "ERROR: development does not resolve"; exit 2; }; \
	MASTER_SHA="$$(git -C "$$MAIN_PATH" rev-parse --verify master^{commit})" || { echo "ERROR: canonical master does not resolve"; exit 2; }; \
	PROJECT_NAMESPACE="$${GLUDD_PROJECT_NAMESPACE:-}"; \
	if [ -z "$$PROJECT_NAMESPACE" ]; then PROJECT_NAMESPACE="$$($(PYTHON) scripts/resource_arbiter.py namespace)"; fi; \
	LOCAL_ATTESTATION="$${GLUDD_RESOURCE_ROOT:-$${TMPDIR:-/tmp}/gludd-resources}/$$PROJECT_NAMESPACE/ci-shards/attestation.json"; \
	[ "$$CURRENT_SHA" = "$$DEV_SHA" ] || { echo "ERROR: release-promote must run at the exact development tip"; exit 2; }; \
	$(MAKE) --no-print-directory worktree-guard; \
	$(MAKE) --no-print-directory main-worktree-guard; \
	git -C "$$MAIN_PATH" merge-base --is-ancestor "$$MASTER_SHA" "$$DEV_SHA" || { echo "ERROR: master cannot fast-forward to development"; exit 2; }; \
	if [ "$(RELEASE_PROMOTE_VALIDATE_ONLY)" = "1" ]; then \
		$(MAKE) --no-print-directory require-dual-track-green SHA="$$DEV_SHA" CI_BRANCH=development DUAL_TRACK_CI_LOCAL_ATTESTATION="$$LOCAL_ATTESTATION" DUAL_TRACK_CI_VALIDATE_ONLY=1; \
		$(MAKE) --no-print-directory release-readiness TAG="$(TAG)" RELEASE_READINESS_VALIDATE_ONLY=1 RELEASE_COMPLETED_STAGES= RELEASE_OBSERVATIONS= REVIEWED_HEAD_INTEGRATION_RECEIPT="$(REVIEWED_HEAD_INTEGRATION_RECEIPT)" RELEASE_CANDIDATE_SHA="$$DEV_SHA"; \
		echo "RELEASE-PROMOTE-VALIDATED tag=$(TAG) master=$$MASTER_SHA development=$$DEV_SHA mode=ff-only"; \
		exit 0; \
	fi; \
	[ -f "$$LOCAL_ATTESTATION" ] || { echo "ERROR: development local attestation is missing: $$LOCAL_ATTESTATION"; exit 2; }; \
	$(MAKE) --no-print-directory require-dual-track-green SHA="$$DEV_SHA" CI_BRANCH=development DUAL_TRACK_CI_LOCAL_ATTESTATION="$$LOCAL_ATTESTATION" DUAL_TRACK_CI_VALIDATE_ONLY=0; \
	$(MAKE) --no-print-directory release-readiness TAG="$(TAG)" RELEASE_READINESS_VALIDATE_ONLY=0 RELEASE_COMPLETED_STAGES= RELEASE_OBSERVATIONS= REVIEWED_HEAD_INTEGRATION_RECEIPT="$(REVIEWED_HEAD_INTEGRATION_RECEIPT)" RELEASE_CANDIDATE_SHA="$$DEV_SHA"; \
	git -C "$$MAIN_PATH" merge --ff-only "$$DEV_SHA"; \
	$(MAKE) --no-print-directory -C "$$MAIN_PATH" release-cut TAG="$(TAG)" MSG="$(MSG)" REVIEWED_HEAD_INTEGRATION_RECEIPT="$(if $(strip $(REVIEWED_HEAD_INTEGRATION_RECEIPT)),$(abspath $(REVIEWED_HEAD_INTEGRATION_RECEIPT)),)" RELEASE_CANDIDATE_SHA="$$DEV_SHA" RELEASE_CI_BRANCH=development RELEASE_LOCAL_ATTESTATION="$$LOCAL_ATTESTATION"

# Background the long local dual-track CI evidence producer so the orchestrator
# does not hold the main thread for hours. Not listed in help because it is an
# internal pipeline helper paired with test-ci-dual-track-local-status.
test-ci-dual-track-local-bg:
	@mkdir -p .gate-logs
	@PID_FILE=".gate-logs/ci-dual-track-local.pid"; \
	STALE_PID=$$(cat "$$PID_FILE" 2>/dev/null || echo ""); \
	if [ -n "$$STALE_PID" ] && kill -0 "$$STALE_PID" 2>/dev/null; then \
		echo "CI-DUAL-TRACK-LOCAL-BG already running pid=$$STALE_PID"; \
		exit 0; \
	fi; \
	rm -f "$$PID_FILE"; \
	LOG=".gate-logs/ci-dual-track-local-$$(date +%Y%m%d%H%M%S).log"; \
	nohup $(MAKE) test-ci-dual-track-local PYTEST_ARGS='' MAX_FILES_PER_BATCH=64 > "$$LOG" 2>&1 & echo $$! | tee "$$PID_FILE"; \
	echo "CI-DUAL-TRACK-LOCAL-BG pid=$$(cat $$PID_FILE) log=$$LOG"

test-ci-dual-track-local-status:
	@PID_FILE=".gate-logs/ci-dual-track-local.pid"; \
	PID=$$(cat "$$PID_FILE" 2>/dev/null || echo ""); \
	RUNNING=false; \
	if [ -n "$$PID" ] && kill -0 "$$PID" 2>/dev/null; then RUNNING=true; fi; \
	if [ "$$RUNNING" = "true" ]; then \
		echo "CI-DUAL-TRACK-LOCAL-BG running pid=$$PID"; \
		LOG=$$(ls -t .gate-logs/ci-dual-track-local-*.log 2>/dev/null | head -1); \
		if [ -n "$$LOG" ]; then \
			echo "latest log: $$LOG"; \
			tail -20 "$$LOG"; \
		fi; \
	else \
		echo "CI-DUAL-TRACK-LOCAL-BG not running"; \
		if [ -n "$$PID" ]; then rm -f "$$PID_FILE"; fi; \
	fi; \
	RESOURCE_ROOT="$$($(PYTHON) scripts/resource_arbiter.py root)"; \
	ATTESTATION="$$RESOURCE_ROOT/ci-shards/attestation.json"; \
	if [ -f "$$ATTESTATION" ]; then echo "ATTESTATION present: $$ATTESTATION"; else echo "ATTESTATION missing: $$ATTESTATION"; fi
