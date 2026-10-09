# --- Proactive bug scanner: find issues before the user does ---
proactive-scan:
	@$(UV) run python scripts/proactive_bug_scan.py

# --- Dispatch dedup: cross-reference /tmp/gludd-dispatched-tasks.json against TASKS.md completed items ---
check-dispatch-dedup:
	@$(UV) run python scripts/check_dispatch_dedup.py

# --- Dispatch diversity: validate wave shape (10 count, ≥3 topics, ≤50% concentration, ≥1 continuation) ---
check-dispatch-diversity:
	@$(UV) run python scripts/check_dispatch_diversity.py $(FILE)

# --- Dead-code detection: flag classes/functions in src/ never imported in production code ---
check-dead-code:
	@$(UV) run python scripts/check_dead_code.py
check-dead-code-json:
	@$(UV) run python scripts/check_dead_code.py --json
check-dead-code-quiet:
	@$(UV) run python scripts/check_dead_code.py --quiet

dead-code-baseline:
	@$(UV) run python scripts/check_dead_code.py --update-baseline

# --- Test env-write lint: forbid bare os.environ[...] = in tests/ (use monkeypatch.setenv) ---
check-test-env-writes:
	@$(UV) run python scripts/check_test_env_writes.py tests

# --- TDD compliance: new/modified source files require corresponding tests ---
# Blocks commit if source files in src/general_ludd/ lack test files with imports + test_* functions.
check-tdd-compliance:
	@$(UV) run python scripts/check_tdd_compliance.py

# --- Coverage gaps: flag modules with missing/stub/no-import test files ---
check-coverage-gaps:
	@$(UV) run python scripts/check_coverage_gaps.py --baseline

check-coverage-gaps-json:
	@$(UV) run python scripts/check_coverage_gaps.py --baseline --json

generate-coverage-gaps-baseline:
	@$(UV) run python scripts/check_coverage_gaps.py --generate-baseline

check-coverage-missing:
	@$(UV) run python scripts/check_coverage_missing.py

integration-health:
	@PROJECT_NAMESPACE="$$($(PYTHON) scripts/resource_arbiter.py namespace)"; \
	PROJECT_KEY="$$($(PYTHON) -c 'import hashlib, sys; print(hashlib.sha256(sys.argv[1].encode()).hexdigest()[:8])' "$$PROJECT_NAMESPACE")"; \
	BT="/tmp/gi-$$PROJECT_KEY-$$$$"; rm -rf "$$BT"; trap 'rm -rf "$$BT"' EXIT; \
	PYTEST_ADDOPTS="$${PYTEST_ADDOPTS:-} --basetemp=$$BT" $(UV) run python scripts/check_integration_health.py

_integration-health-watchdog-owned-gate:
	@PROJECT_NAMESPACE="$$($(PYTHON) scripts/resource_arbiter.py namespace)"; \
	PROJECT_KEY="$$($(PYTHON) -c 'import hashlib, sys; print(hashlib.sha256(sys.argv[1].encode()).hexdigest()[:8])' "$$PROJECT_NAMESPACE")"; \
	BT="/tmp/gi-$$PROJECT_KEY-$$$$"; rm -rf "$$BT"; trap 'rm -rf "$$BT"' EXIT; \
	TMPDIR="$$BT" PYTEST_ADDOPTS="$${PYTEST_ADDOPTS:-} --basetemp=$$BT" $(UV) run python scripts/check_integration_health.py --watchdog-owned-gate

integration-health-watch:
	@while true; do \
		echo "[$$(date -u +%Y-%m-%dT%H:%M:%SZ)] Running integration-health..."; \
		$(UV) run python scripts/check_integration_health.py; \
		RC=$$?; \
		if [ $$RC -ne 0 ]; then \
			echo "[$$(date -u +%Y-%m-%dT%H:%M:%SZ)] FAILURES DETECTED (exit $$RC)"; \
			cat /tmp/gludd-integration-failures.json 2>/dev/null || true; \
		else \
			echo "[$$(date -u +%Y-%m-%dT%H:%M:%SZ)] PASS"; \
		fi; \
		sleep 10; \
	done

integration-health-background:
	@echo "[integration-health-background] Launching integration-health via nohup..."
	@nohup $(MAKE) integration-health > /tmp/gludd-integration-run.log 2>&1 & \
	echo "  PID: $$!"; \
	echo "  Log: /tmp/gludd-integration-run.log"; \
	echo "  Failures JSON: /tmp/gludd-integration-failures.json"

integration-health-report:
	@sleep 300; \
	if [ -f /tmp/gludd-integration-failures.json ]; then \
		echo "=== FAILURES ==="; \
		cat /tmp/gludd-integration-failures.json; \
	else \
		echo "=== LOG (last 100 lines) ==="; \
		tail -100 /tmp/gludd-integration-run.log; \
	fi

# --- Audit untested code: plugins with no tests, hooks without test coverage, Python modules without tests ---
audit-untested-code:
	@$(UV) run python scripts/audit_untested_code.py

gate-all: gate-refresh
	@$(MAKE) --no-print-directory gate-release-phases

check-test-quality:
	@$(UV) run python scripts/check_test_quality.py

# --- Type strictness: flag `Any` usage in Python annotations (tight types only) ---
# Scans src/ for Any in return/param/annassign annotations (incl. nested dict[...]/Optional[...]).
# See .opencode/skills/type-safety/SKILL.md for the full policy.
check-types:
	@$(UV) run python scripts/check_type_strictness.py src/

# Same scan, but tolerate pre-existing violations listed one-per-line as `path:line`
# in the baseline file. Use this to enforce the gate on NEW code only.
check-types-baseline:
	@$(UV) run python scripts/check_type_strictness.py src/ --baseline config/type_any_baseline.txt

verify-feature-claims:
	@echo "=== verify-feature-claims: fast evidence verification (file-existence for test: refs) ==="
	@$(UV) run ansible-playbook -i localhost, -c local playbooks/verify_feature_claims.yml

file-executable:
	@if [ -f "$(FILE)" ]; then chmod +x "$(FILE)"; else echo "ERROR: FILE '$(FILE)' not found"; exit 1; fi

dist: build-executable bundle-binaries sbom
	@echo "Assembling tarball..."
	@chmod +x dist/install.sh
	@rm -rf $(TARBALL_DIR)
	@mkdir -p $(TARBALL_DIR)
	@cp dist/gludd $(TARBALL_DIR)/gludd
	@cp dist/install.sh $(TARBALL_DIR)/install.sh
	@if [ -f dist/general-ludd.service ]; then cp dist/general-ludd.service $(TARBALL_DIR)/general-ludd.service; fi
	@if [ -f dist/README.md ]; then cp dist/README.md $(TARBALL_DIR)/README.md; fi
	@cp -r config $(TARBALL_DIR)/config
	@cp -r templates $(TARBALL_DIR)/templates
	@cp -r dist/binaries $(TARBALL_DIR)/binaries 2>/dev/null || true
	@echo "Packing license + SBOM (W5.2)..."
	@cp LICENSE $(TARBALL_DIR)/LICENSE
	@if [ ! -f THIRD_PARTY_LICENSES.md ]; then echo "ERROR: THIRD_PARTY_LICENSES.md missing"; exit 1; fi
	@cp THIRD_PARTY_LICENSES.md $(TARBALL_DIR)/THIRD_PARTY_LICENSES.md
	@echo "Scrubbing build-machine paths from SBOM (W5.3)..."
	@$(UV) run python -c "import re,pathlib; p=pathlib.Path('dist/sbom.json'); t=p.read_text(); import os; t=t.replace('file://'+os.getcwd(),'file:///opt/general-ludd').replace(os.getcwd(),'/opt/general-ludd'); pathlib.Path('$(TARBALL_DIR)/sbom.json').write_text(t)"
	@mkdir -p $(TARBALL_DIR)/docs
	@if [ -f docs/quickstart.md ]; then cp docs/quickstart.md $(TARBALL_DIR)/docs/; fi
	@if [ -f docs/configuration.md ]; then cp docs/configuration.md $(TARBALL_DIR)/docs/; fi
	@if [ -f docs/architecture.md ]; then cp docs/architecture.md $(TARBALL_DIR)/docs/; fi
	@if [ -f docs/model-setup.md ]; then cp docs/model-setup.md $(TARBALL_DIR)/docs/; fi
	@echo "Verifying no build-machine paths leaked into the tarball dir (W5.3)..."
	@if grep -rIl -e '/Users/' -e 'Mac.localdomain' $(TARBALL_DIR) 2>/dev/null; then \
		echo "ERROR: absolute local paths leaked into $(TARBALL_DIR)"; exit 1; \
	else echo "Tarball dir is path-clean."; fi
	@cd dist && tar czf $(TARBALL_NAME).tar.gz $(TARBALL_NAME)
	@cd dist && shasum -a 256 $(TARBALL_NAME).tar.gz > $(TARBALL_NAME).tar.gz.sha256
	@echo "Created dist/$(TARBALL_NAME).tar.gz"
	@echo "Checksum: dist/$(TARBALL_NAME).tar.gz.sha256"

dist-clean:
	@rm -rf dist/general-ludd-agent-* dist/hottentot-agent-* dist/gludd dist/hottentot dist/deb-root dist/gludd_*.deb dist/gludd_*.deb.sha256 dist/linux build

deb-package:
	@echo "=== Building .deb package ==="
	@which dpkg-deb >/dev/null 2>&1 || (echo "ERROR: dpkg-deb not found. This target requires a Debian-based system."; exit 1)
	@mkdir -p dist/deb-root/DEBIAN dist/deb-root/usr/bin
	@cp dist/gludd dist/deb-root/usr/bin/gludd
	@chmod 755 dist/deb-root/usr/bin/gludd
	@sed "s/VERSION_PLACEHOLDER/$(VERSION)/" dist/debian/control > dist/deb-root/DEBIAN/control
	@dpkg-deb --build dist/deb-root "dist/gludd_$(VERSION)_amd64.deb"
	@sha256sum "dist/gludd_$(VERSION)_amd64.deb" > "dist/gludd_$(VERSION)_amd64.deb.sha256"
	@echo "=== .deb built: dist/gludd_$(VERSION)_amd64.deb ==="

deb-install-deps:
	@echo "=== Installing .deb package dependencies ==="
	@grep '^Depends:' dist/debian/control | sed 's/^Depends: //' | tr ',' '\n' | sed 's/^ *//;s/ .*//' | xargs -r sudo apt-get install -y

RPMBUILD_DIR := $(abspath dist/rpmbuild)
rpm-package:
	@echo "=== Building .rpm package ==="
	@which rpmbuild >/dev/null 2>&1 || (echo "ERROR: rpmbuild not found. Install rpm-build package."; exit 1)
	@rm -rf "$(RPMBUILD_DIR)"
	@mkdir -p dist/rpm "$(RPMBUILD_DIR)/BUILD" "$(RPMBUILD_DIR)/BUILDROOT" "$(RPMBUILD_DIR)/RPMS" "$(RPMBUILD_DIR)/SOURCES" "$(RPMBUILD_DIR)/SPECS" "$(RPMBUILD_DIR)/SRPMS"
	@cp dist/gludd "$(RPMBUILD_DIR)/SOURCES/gludd"
	@sed "s/VERSION_PLACEHOLDER/$(VERSION)/g" dist/rpm/gludd.spec > "$(RPMBUILD_DIR)/SPECS/gludd.spec"
	@rpmbuild -bb --define "_topdir $(RPMBUILD_DIR)" "$(RPMBUILD_DIR)/SPECS/gludd.spec"
	@RPM_FILE=$$(ls "$(RPMBUILD_DIR)"/RPMS/x86_64/gludd-*.rpm 2>/dev/null | head -1); \
	if [ -z "$$RPM_FILE" ]; then echo "ERROR: rpmbuild produced no .rpm"; exit 1; fi; \
	cp "$$RPM_FILE" "dist/gludd-$(VERSION)-1.x86_64.rpm"; \
	sha256sum "dist/gludd-$(VERSION)-1.x86_64.rpm" > "dist/gludd-$(VERSION)-1.x86_64.rpm.sha256"; \
	rm -rf "$(RPMBUILD_DIR)"
	@echo "=== .rpm built: dist/gludd-$(VERSION)-1.x86_64.rpm ==="

# --- macOS .dmg packaging ---
# Builds a read-only compressed .dmg from the PyInstaller binary.
# Only runs on macOS (requires hdiutil).
DMG_NAME := gludd-$(VERSION)-macos-arm64.dmg
DMG_VOLUME := gludd-install
macos-dmg: build-executable
	@if [ "$$(uname -s)" != "Darwin" ]; then echo "macos-dmg requires macOS (hdiutil)"; exit 1; fi
	@echo "Building $(DMG_NAME)..."
	@rm -f dist/$(DMG_NAME)
	@mkdir -p dist/dmg-staging
	@cp dist/gludd dist/dmg-staging/gludd
	@cp -r config dist/dmg-staging/config
	@cp -r templates dist/dmg-staging/templates
	@cp -r playbooks dist/dmg-staging/playbooks
	@cp dist/install.sh dist/dmg-staging/install.sh 2>/dev/null || true
	@hdiutil create -fs HFS+ -volname $(DMG_VOLUME) -srcfolder dist/dmg-staging -format UDZO dist/$(DMG_NAME)
	@rm -rf dist/dmg-staging
	@shasum -a 256 dist/$(DMG_NAME) > dist/$(DMG_NAME).sha256
	@echo "Created dist/$(DMG_NAME)"
	@echo "Checksum: dist/$(DMG_NAME).sha256"

# --- Windows NSIS installer ---
# Creates a Windows installer .exe from the PyInstaller binary.
# Requires makensis (NSIS). Install: brew install makensis or apt install nsis.
NSI_SCRIPT := dist/windows/gludd.nsi
WINDOWS_INSTALLER := gludd-$(VERSION)-setup-x86_64.exe
windows-installer:
	@if ! command -v makensis >/dev/null 2>&1; then echo "windows-installer requires makensis (NSIS). Install: brew install makensis or apt install nsis"; exit 1; fi
	@echo "Building $(WINDOWS_INSTALLER)..."
	@mkdir -p dist/windows
	@if [ -f dist/gludd.exe ]; then cp dist/gludd.exe dist/windows/gludd.exe; elif [ -f dist/gludd ]; then cp dist/gludd dist/windows/gludd.exe; else echo "ERROR: no gludd binary found at dist/gludd.exe or dist/gludd"; exit 1; fi
	@makensis -WX -DVERSION=$(VERSION) -DBUILDDIR=.. $(NSI_SCRIPT)
	@shasum -a 256 dist/$(WINDOWS_INSTALLER) > dist/$(WINDOWS_INSTALLER).sha256
	@echo "Created dist/$(WINDOWS_INSTALLER)"
	@echo "Checksum: dist/$(WINDOWS_INSTALLER).sha256"

# --- Build all release platform packages locally for testing ---
# Calls all packaging targets. Each is skipped if the host lacks the tool.
release-artifacts: build-executable
	@echo "=== Building all platform packages for testing ==="
	@echo "  Platform: $$(uname -s)-$$(uname -m)"
	@echo ""
	@# macOS .dmg
	@if [ "$$(uname -s)" = "Darwin" ]; then $(MAKE) -s macos-dmg; else echo "[skip] macOS .dmg (requires macOS)"; fi
	@# Windows NSIS installer
	@if command -v makensis >/dev/null 2>&1; then $(MAKE) -s windows-installer; else echo "[skip] Windows installer (makensis not found)"; fi
	@# Linux .deb
	@if command -v dpkg-deb >/dev/null 2>&1; then $(MAKE) -s deb-package; else echo "[skip] .deb (dpkg-deb not found)"; fi
	@# Linux .rpm
	@if command -v rpmbuild >/dev/null 2>&1; then $(MAKE) -s rpm-package; else echo "[skip] .rpm (rpmbuild not found)"; fi
	@echo ""
	@echo "=== release-artifacts complete ==="
	@ls -la dist/*.dmg dist/*.deb dist/*.rpm dist/*-setup*.exe 2>/dev/null || echo "(some artifacts skipped — this is normal)"

bundle-binaries: bundle-ripgrep
	@echo "Bundling OpenBao and OpenTofu binaries into dist/binaries..."
	@mkdir -p dist/binaries
	@$(UV) run python scripts/download_bundled_binaries.py || echo "Some binaries could not be downloaded (network unavailable?). The dist will still include what was bundled."

# Bundle a SHA-pinned, musl-static ripgrep (BurntSushi) into dist/binaries/rg.
# The musl-static x86_64-linux build is fully self-contained (no libc dep), which
# is what the dist tarball / container ships. The download is checksum-verified
# fail-closed: if shasum -c does not match RG_SHA256 the staged binary is removed
# and the target exits non-zero, so a corrupted/MITM'd download can never be
# bundled. Locating it at runtime is handled by BinaryBootstrapper.get_bundled_
# binary_path('rg') -> dist/binaries/rg (see code_intelligence/rg_search.py).
RG_VERSION ?= 14.1.1
RG_PLATFORM ?= x86_64-unknown-linux-musl
RG_ARCHIVE := ripgrep-$(RG_VERSION)-$(RG_PLATFORM).tar.gz
RG_URL := https://github.com/BurntSushi/ripgrep/releases/download/$(RG_VERSION)/$(RG_ARCHIVE)
# Official digest from the ripgrep release asset $(RG_ARCHIVE).sha256
# (github.com/BurntSushi/ripgrep/releases/tag/$(RG_VERSION)). Update alongside
# RG_VERSION/RG_PLATFORM; a mismatch fails closed and nothing is bundled.
RG_SHA256 ?= 4cf9f2741e6c465ffdb7c26f38056a59e2a2544b51f7cc128ef28337eeae4d8e
bundle-ripgrep:
	@echo "Bundling ripgrep $(RG_VERSION) ($(RG_PLATFORM)) into dist/binaries/rg..."
	@mkdir -p dist/binaries
	@tmp=$$(mktemp -d) && trap 'rm -rf "$$tmp"' EXIT && \
		echo "  downloading $(RG_URL)" && \
		curl -fsSL "$(RG_URL)" -o "$$tmp/$(RG_ARCHIVE)" && \
		echo "$(RG_SHA256)  $$tmp/$(RG_ARCHIVE)" > "$$tmp/rg.sha256" && \
		if ! shasum -a 256 -c "$$tmp/rg.sha256"; then \
			echo "ERROR: ripgrep checksum mismatch (expected $(RG_SHA256)) — refusing to bundle"; \
			exit 1; \
		fi && \
		tar xzf "$$tmp/$(RG_ARCHIVE)" -C "$$tmp" && \
		cp "$$tmp/ripgrep-$(RG_VERSION)-$(RG_PLATFORM)/rg" dist/binaries/rg && \
		chmod +x dist/binaries/rg && \
		echo "  bundled -> dist/binaries/rg" || \
		{ echo "WARNING: ripgrep bundle failed (network unavailable or sha unset?); search degrades to in-process"; rm -f dist/binaries/rg; }

# install cmake via brew
install-cmake:
	@command -v cmake >/dev/null 2>&1 && { cmake --version | head -1; exit 0; } || true
	@command -v brew >/dev/null 2>&1 || { echo "brew MISSING — cannot install cmake"; exit 1; }
	@echo "Installing cmake via brew ..."
	@brew install cmake 2>&1 | tail -5 || echo "brew-install-cmake-failed"
	@command -v cmake >/dev/null 2>&1 && cmake --version | head -1 || echo "cmake still missing after install"

# Build llama-quantize from source into external/llamacpp/build/bin/.
# Clones llama.cpp shallow if not already present, then cmake-builds just the
# quantize tool.  Gracefully skips (exit 0) when cmake or a C++ compiler is
# missing — tests that depend on the binary will skip too.
_LLAMACPP_URL ?= https://github.com/ggerganov/llama.cpp.git
build-llamacpp-tools:
	@echo "=== Building llama.cpp tools ==="
	@mkdir -p external
	@if [ ! -d external/llamacpp ]; then \
		echo "  cloning llama.cpp (shallow)..."; \
		git clone --depth 1 $(_LLAMACPP_URL) external/llamacpp; \
	fi
	@if ! command -v cmake >/dev/null 2>&1; then \
		echo "WARNING: cmake not found — cannot build llama.cpp tools"; \
	elif ! command -v cc >/dev/null 2>&1 && ! command -v gcc >/dev/null 2>&1 && ! command -v clang >/dev/null 2>&1; then \
		echo "WARNING: no C compiler found — cannot build llama.cpp tools"; \
	else \
		mkdir -p external/llamacpp/build && \
		cd external/llamacpp/build && \
		cmake .. -DLLAMA_BUILD_TESTS=OFF -DLLAMA_BUILD_EXAMPLES=OFF -DLLAMA_BUILD_SERVER=OFF -DLLAMA_CURL=OFF -DGGML_CUDA=OFF -DGGML_METAL=OFF -DGGML_VULKAN=OFF -DGGML_BLAS=OFF -DGGML_SYCL=OFF && \
		cmake --build . --target llama-quantize -- -j $$(getconf _NPROCESSORS_ONLN 2>/dev/null || sysctl -n hw.ncpu 2>/dev/null || echo 4) && \
		echo "  llama-quantize -> external/llamacpp/build/bin/llama-quantize"; \
	fi

diagnose-e2e-tools:
	@echo "=== E2E Tool Diagnostics ==="
	@$(UV) run python scripts/diagnose_e2e_tools.py

# Restore specific files from a historical ref into the working tree (files
# deleted from HEAD can only come back from history; used to recover dist/
# tarball inputs like install.sh). REF=<sha|ref> FILES='<path> [path ...]'.
git-restore-from:
	@if [ -z "$(REF)" ] || [ -z "$(FILES)" ]; then echo "Usage: make git-restore-from REF=<sha> FILES='<paths>'"; exit 1; fi
	@git checkout $(REF) -- $(FILES)
	@echo "Restored from $(REF): $(FILES)"

container-build:
	@if [ -z "$(CONTAINER_RUNTIME)" ]; then echo "ERROR: podman or docker not found"; exit 1; fi
	@$(CONTAINER_RUNTIME) build -t $(CONTAINER_IMAGE) .

container-run:
	@if [ -z "$(CONTAINER_RUNTIME)" ]; then echo "ERROR: podman or docker not found"; exit 1; fi
	@$(CONTAINER_RUNTIME) run -p 8000:8000 $(CONTAINER_IMAGE)

container-push:
	@if [ -z "$(CONTAINER_RUNTIME)" ]; then echo "ERROR: podman or docker not found"; exit 1; fi
	@$(CONTAINER_RUNTIME) push $(CONTAINER_IMAGE)

# --- VM sandbox image targets (FEATURE_UNIKERNEL_SANDBOX P3) ---

SANDBOX_CACHE ?= $(HOME)/.cache/gludd/sandbox
SANDBOX_IMAGE ?= $(SANDBOX_CACHE)/rootfs.ext4
GLUDD_SANDBOX_STATE_DIR ?=
GLUDD_PROJECT_ROOT ?=

build-sandbox-image:
	@mkdir -p "$(SANDBOX_CACHE)"
	@echo "=== Build sandbox rootfs image (P3 stub) ==="
	@$(UV) run python -c "from general_ludd.security.sandboxes.vm.image_builder import build_rootfs; build_rootfs('$(SANDBOX_IMAGE)')"
	@echo "Sandbox image stub written to $(SANDBOX_IMAGE)"

vm-image-build: VM_TYPE ?= all
vm-image-build:
	@mkdir -p "$(SANDBOX_CACHE)"
	@echo "=== Build VM sandbox images (type=$(VM_TYPE)) ==="
	@if [ "$(VM_TYPE)" = "all" ] || [ "$(VM_TYPE)" = "firecracker" ]; then \
		$(UV) run python -c "from general_ludd.security.sandboxes.vm.image_builder import ImageManifest, build_rootfs; m = ImageManifest(name='gludd-sandbox-firecracker', packages=('python3','ansible','git'), architecture='x86_64'); r = build_rootfs('$(SANDBOX_CACHE)/firecracker-rootfs.ext4', 'firecracker', m); print(f'Firecracker: {r.path} ({r.size_bytes} bytes, hash={r.manifest_hash[:12]})')"; \
	fi
	@if [ "$(VM_TYPE)" = "all" ] || [ "$(VM_TYPE)" = "gvisor" ]; then \
		$(UV) run python -c "from general_ludd.security.sandboxes.vm.image_builder import ImageManifest, build_rootfs; m = ImageManifest(name='gludd-sandbox-gvisor', packages=('python3','ansible','git'), architecture='x86_64'); r = build_rootfs('$(SANDBOX_CACHE)/gvisor-bundle', 'gvisor', m); print(f'gVisor: {r.path} ({r.size_bytes} bytes, hash={r.manifest_hash[:12]})')"; \
	fi
	@echo "VM images built under $(SANDBOX_CACHE)"

vm-image-list:
	@echo "=== Cached VM sandbox images ==="
	@$(UV) run python -c "from general_ludd.security.sandboxes.vm.image_builder import list_cached_images; entries = list_cached_images(); [print(f'{e[\"hash\"][:12]}  {e[\"type\"]:14s}  {e[\"name\"]}  {e[\"size_bytes\"]} bytes') for e in entries]"

vm-image-clean:
	@echo "=== Clean VM sandbox image cache ==="
	@$(UV) run python -c "from general_ludd.security.sandboxes.vm.image_builder import cleanup_cache; n = cleanup_cache(max_age_seconds=0); print(f'Removed {n} cached image(s)')"

verify-sandbox-image:
	@echo "=== Verify sandbox rootfs image ($(SANDBOX_IMAGE)) ==="
	@$(UV) run python -c "from general_ludd.security.sandboxes.vm.image_builder import verify_image; ok = verify_image('$(SANDBOX_IMAGE)'); print('PASS' if ok else 'FAIL (image missing or corrupted)'); exit(0 if ok else 1)"

clean-sandbox-images:
	@echo "=== Clean cached sandbox images ==="
	@rm -rf "$(SANDBOX_CACHE)"
	@echo "Removed $(SANDBOX_CACHE)"

# --- FEATURE_SANDBOX_STATE_ROOT: host-side sandbox runtime-state directory ---

sandbox-state-dir:
	@export GLUDD_SANDBOX_STATE_DIR="$(GLUDD_SANDBOX_STATE_DIR)" && export GLUDD_PROJECT_ROOT="$(GLUDD_PROJECT_ROOT)" && $(UV) run python -c "from general_ludd.security.sandboxes.state import SandboxState; s = SandboxState.discover(); print(s.project_dir)"

sandbox-state-list:
	@export GLUDD_SANDBOX_STATE_DIR="$(GLUDD_SANDBOX_STATE_DIR)" && export GLUDD_PROJECT_ROOT="$(GLUDD_PROJECT_ROOT)" && $(UV) run python -c "from general_ludd.security.sandboxes.state import SandboxState; s = SandboxState.discover(); from pathlib import Path; [print(str(p.relative_to(s.project_dir)) if p.is_relative_to(s.project_dir) else str(p)) for p in sorted(s.project_dir.rglob('*'))] if s.project_dir.exists() else print('(empty)')"

sandbox-state-clean:
	@export GLUDD_SANDBOX_STATE_DIR="$(GLUDD_SANDBOX_STATE_DIR)" && export GLUDD_PROJECT_ROOT="$(GLUDD_PROJECT_ROOT)" && $(UV) run python -c "from general_ludd.security.sandboxes.state import SandboxState; s = SandboxState.discover(create=False); removed = s.cleanup_project() if s.project_dir.exists() else False; print(f'Removed {s.project_dir}' if removed else '(nothing to clean)')"

# Reproduce CI's Linux "Gate" step locally — no GitHub login needed. Runs the
# EXACT CI command (make lint typecheck test-count test smoke) inside a Linux
# python container so platform-specific failures (tests skipped on macOS but run
# on Linux, etc.) surface here directly instead of only in CI. PYV=3.11|3.12.
# Uses a container-local venv (UV_PROJECT_ENVIRONMENT) so the host macOS .venv is
# never touched. Streams via tee (observability invariant).
LIMA_INSTANCE ?= gludd-docker
LIMA_IMAGE ?= ubuntu:24.04
LIMA_DOCKER_CONFIG ?= /tmp/gludd-lima-docker-config
LIMA_DOCKER_TEMPLATE ?= template:docker
LIMA_DOCKER_VALIDATE_ONLY ?= 0
LIMA_DOCKER_START_TIMEOUT_SECS ?= 180
LIMA_DOCKER_STOP_TIMEOUT_SECS ?= 200
LIMA_DOCKER_STOP_KILL_AFTER_SECS ?= 10
LIMA_DOCKER_DELETE_TIMEOUT_SECS ?= 240
LIMA_DOCKER_DELETE_KILL_AFTER_SECS ?= 10
LIMA_DOCKER_DELETE_VALIDATE_ONLY ?= 1
LIMA_DOCKER_DELETE_CONFIRM ?=
PODMAN_MACHINE ?= gludd
VDISK ?= 20
PODMAN_LEGACY_MACHINE ?= podman-machine-default
PODMAN_LEGACY_DELETE_VALIDATE_ONLY ?= 1
PODMAN_LEGACY_DELETE_TIMEOUT_SECS ?= 120

.PHONY: lima-docker-ensure
lima-docker-ensure: ## Provision, start, or reuse one namespaced Lima Docker VM and prove engine readiness
	@case "$(LIMA_DOCKER_VALIDATE_ONLY)" in 0|1) ;; *) echo "LIMA_DOCKER_VALIDATE_ONLY must be 0 or 1"; exit 2;; esac
	@[ "$(LIMA_DOCKER_START_TIMEOUT_SECS)" -ge 1 ] 2>/dev/null || { echo "LIMA_DOCKER_START_TIMEOUT_SECS must be a positive integer"; exit 2; }
	@case "$(LIMA_INSTANCE)" in \
		""|*[!A-Za-z0-9._-]*|.|..) echo "Refusing invalid Lima instance name: $(LIMA_INSTANCE)"; exit 2;; \
		gludd-*) ;; \
		*) echo "Refusing non-Gludd Lima instance: $(LIMA_INSTANCE)"; exit 2;; \
	esac
	@case "$(LIMA_DOCKER_TEMPLATE)" in template:docker) ;; *) echo "Refusing unreviewed Lima Docker template: $(LIMA_DOCKER_TEMPLATE)"; exit 2;; esac
	@if [ "$(LIMA_DOCKER_VALIDATE_ONLY)" = "1" ]; then \
		echo "LIMA_DOCKER_ENSURE_VALID instance=$(LIMA_INSTANCE) template=$(LIMA_DOCKER_TEMPLATE) config=$(LIMA_DOCKER_CONFIG) timeout_secs=$(LIMA_DOCKER_START_TIMEOUT_SECS)"; \
		exit 0; \
	fi; \
	record=$$(limactl list "$(LIMA_INSTANCE)" --format '{{.Name}}|{{.Status}}' 2>/dev/null || true); \
	if [ -z "$$record" ]; then \
		echo "LIMA_DOCKER_ENSURE_CREATE instance=$(LIMA_INSTANCE) template=$(LIMA_DOCKER_TEMPLATE)"; \
		limactl start --name "$(LIMA_INSTANCE)" --timeout "$(LIMA_DOCKER_START_TIMEOUT_SECS)s" --progress "$(LIMA_DOCKER_TEMPLATE)"; \
	else \
		name=$${record%%|*}; status=$${record#*|}; \
		if [ "$$name" != "$(LIMA_INSTANCE)" ] || [ "$$record" = "$$status" ]; then \
			echo "Refusing ambiguous Lima instance record: $$record"; exit 2; \
		fi; \
		case "$$status" in \
			Running) echo "LIMA_DOCKER_ENSURE_REUSE instance=$(LIMA_INSTANCE) status=$$status";; \
			Stopped) echo "LIMA_DOCKER_ENSURE_START instance=$(LIMA_INSTANCE)"; limactl start --timeout "$(LIMA_DOCKER_START_TIMEOUT_SECS)s" --progress "$(LIMA_INSTANCE)";; \
			*) echo "Refusing Lima instance in nonterminal lifecycle state: $$record"; exit 1;; \
		esac; \
	fi; \
	socket=$$(limactl list "$(LIMA_INSTANCE)" --format '{{.Dir}}/sock/docker.sock' 2>/dev/null || true); \
	if [ -z "$$socket" ]; then echo "Lima Docker socket path unavailable after ensure for $(LIMA_INSTANCE)"; exit 1; fi; \
	mkdir -p "$(LIMA_DOCKER_CONFIG)"; \
	chmod 700 "$(LIMA_DOCKER_CONFIG)"; \
	DOCKER_CONFIG="$(LIMA_DOCKER_CONFIG)" DOCKER_HOST="unix://$$socket" docker info --format 'server={{.ServerVersion}} containers={{.Containers}} images={{.Images}}'; \
	echo "LIMA_DOCKER_ENSURE_READY instance=$(LIMA_INSTANCE) socket=$$socket"

lima-docker-start: ## Start only an existing namespaced Lima Docker VM and prove engine readiness
	@case "$(LIMA_DOCKER_VALIDATE_ONLY)" in 0|1) ;; *) echo "LIMA_DOCKER_VALIDATE_ONLY must be 0 or 1"; exit 2;; esac
	@[ "$(LIMA_DOCKER_START_TIMEOUT_SECS)" -ge 1 ] 2>/dev/null || { echo "LIMA_DOCKER_START_TIMEOUT_SECS must be a positive integer"; exit 2; }
	@if [ "$(LIMA_DOCKER_VALIDATE_ONLY)" = "1" ]; then \
		echo "LIMA_DOCKER_START_VALID instance=$(LIMA_INSTANCE) config=$(LIMA_DOCKER_CONFIG) timeout_secs=$(LIMA_DOCKER_START_TIMEOUT_SECS)"; \
		exit 0; \
	fi; \
	instance=$$(limactl list "$(LIMA_INSTANCE)" --format '{{.Name}}' 2>/dev/null || true); \
	if [ "$$instance" != "$(LIMA_INSTANCE)" ]; then \
		echo "Refusing to create an unprovisioned Lima instance: $(LIMA_INSTANCE)"; \
		exit 1; \
	fi; \
	echo "Starting existing Lima Docker VM $(LIMA_INSTANCE) (timeout $(LIMA_DOCKER_START_TIMEOUT_SECS)s)"; \
	limactl start --timeout "$(LIMA_DOCKER_START_TIMEOUT_SECS)s" "$(LIMA_INSTANCE)"; \
	socket=$$(limactl list "$(LIMA_INSTANCE)" --format '{{.Dir}}/sock/docker.sock' 2>/dev/null || true); \
	if [ -z "$$socket" ] || [ ! -S "$$socket" ]; then \
		echo "Lima Docker socket unavailable after startup for $(LIMA_INSTANCE): $$socket"; \
		exit 1; \
	fi; \
	mkdir -p "$(LIMA_DOCKER_CONFIG)"; \
	chmod 700 "$(LIMA_DOCKER_CONFIG)"; \
	DOCKER_CONFIG="$(LIMA_DOCKER_CONFIG)" DOCKER_HOST="unix://$$socket" docker info --format 'server={{.ServerVersion}} containers={{.Containers}} images={{.Images}}'; \
	echo "LIMA_DOCKER_START_READY instance=$(LIMA_INSTANCE) socket=$$socket"

lima-docker-stop: ## Gracefully stop only an existing Gludd-namespaced Lima VM
	@case "$(LIMA_DOCKER_VALIDATE_ONLY)" in 0|1) ;; *) echo "LIMA_DOCKER_VALIDATE_ONLY must be 0 or 1"; exit 2;; esac
	@[ "$(LIMA_DOCKER_STOP_TIMEOUT_SECS)" -ge 1 ] 2>/dev/null || { echo "LIMA_DOCKER_STOP_TIMEOUT_SECS must be a positive integer"; exit 2; }
	@[ "$(LIMA_DOCKER_STOP_KILL_AFTER_SECS)" -ge 1 ] 2>/dev/null || { echo "LIMA_DOCKER_STOP_KILL_AFTER_SECS must be a positive integer"; exit 2; }
	@case "$(LIMA_INSTANCE)" in \
		""|*[!A-Za-z0-9._-]*|.|..) echo "Refusing invalid Lima instance name: $(LIMA_INSTANCE)"; exit 2;; \
		gludd-*) ;; \
		*) echo "Refusing non-Gludd Lima instance: $(LIMA_INSTANCE)"; exit 2;; \
	esac; \
	if [ "$(LIMA_DOCKER_VALIDATE_ONLY)" = "1" ]; then \
		echo "LIMA_DOCKER_STOP_VALID instance=$(LIMA_INSTANCE) timeout_secs=$(LIMA_DOCKER_STOP_TIMEOUT_SECS) kill_after_secs=$(LIMA_DOCKER_STOP_KILL_AFTER_SECS)"; \
		exit 0; \
	fi; \
	record=$$(limactl list "$(LIMA_INSTANCE)" --format '{{.Name}}|{{.Status}}' 2>/dev/null || true); \
	name=$${record%%|*}; \
	status=$${record#*|}; \
	if [ "$$name" != "$(LIMA_INSTANCE)" ] || [ "$$record" = "$$status" ]; then \
		echo "Refusing to stop missing Lima instance: $(LIMA_INSTANCE)"; \
		exit 1; \
	fi; \
	case "$$status" in \
		Stopped) echo "LIMA_DOCKER_STOP_ALREADY_STOPPED instance=$(LIMA_INSTANCE)"; exit 0;; \
		Running) ;; \
		*) echo "Refusing graceful stop for Lima instance $(LIMA_INSTANCE) in status $$status"; exit 1;; \
	esac; \
	echo "LIMA_DOCKER_STOP_BEGIN instance=$(LIMA_INSTANCE) timeout_secs=$(LIMA_DOCKER_STOP_TIMEOUT_SECS)"; \
	limactl --tty=false stop "$(LIMA_INSTANCE)" & \
	stop_pid=$$!; \
	terminate_owned_stop() { \
		kill -TERM "$$stop_pid" 2>/dev/null || true; \
		grace_elapsed=0; \
		while kill -0 "$$stop_pid" 2>/dev/null && [ "$$grace_elapsed" -lt "$(LIMA_DOCKER_STOP_KILL_AFTER_SECS)" ]; do \
			sleep 1; \
			grace_elapsed=$$((grace_elapsed + 1)); \
		done; \
		if kill -0 "$$stop_pid" 2>/dev/null; then \
			echo "LIMA_DOCKER_STOP_KILL instance=$(LIMA_INSTANCE) kill_after_secs=$(LIMA_DOCKER_STOP_KILL_AFTER_SECS) signal=KILL"; \
			kill -KILL "$$stop_pid" 2>/dev/null || true; \
		fi; \
		wait "$$stop_pid" 2>/dev/null || true; \
	}; \
	trap 'terminate_owned_stop; exit 130' HUP INT TERM; \
	stop_rc=0; \
	elapsed=0; \
	timed_out=0; \
	while kill -0 "$$stop_pid" 2>/dev/null; do \
		if [ "$$elapsed" -ge "$(LIMA_DOCKER_STOP_TIMEOUT_SECS)" ]; then \
			echo "LIMA_DOCKER_STOP_TIMEOUT instance=$(LIMA_INSTANCE) timeout_secs=$(LIMA_DOCKER_STOP_TIMEOUT_SECS) signal=TERM"; \
			terminate_owned_stop; \
			stop_rc=124; \
			timed_out=1; \
			break; \
		fi; \
		sleep 1; \
		elapsed=$$((elapsed + 1)); \
	done; \
	if [ "$$timed_out" -eq 0 ]; then \
		wait "$$stop_pid" || stop_rc=$$?; \
	fi; \
	trap - HUP INT TERM; \
	if [ "$$stop_rc" -ne 0 ]; then \
		echo "Lima Docker shutdown failed or exceeded its bound: rc=$$stop_rc instance=$(LIMA_INSTANCE)"; \
		exit "$$stop_rc"; \
	fi; \
	after=$$(limactl list "$(LIMA_INSTANCE)" --format '{{.Name}}|{{.Status}}' 2>/dev/null || true); \
	if [ "$$after" != "$(LIMA_INSTANCE)|Stopped" ]; then \
		echo "Lima Docker shutdown was not proven: instance=$(LIMA_INSTANCE) observed=$$after"; \
		exit 1; \
	fi; \
	echo "LIMA_DOCKER_STOP_READY instance=$(LIMA_INSTANCE) status=Stopped"

.PHONY: lima-docker-delete
lima-docker-delete: ## Delete only one stopped, exactly confirmed Gludd-namespaced Lima VM
	@case "$(LIMA_DOCKER_DELETE_VALIDATE_ONLY)" in 0|1) ;; *) echo "LIMA_DOCKER_DELETE_VALIDATE_ONLY must be 0 or 1"; exit 2;; esac
	@[ "$(LIMA_DOCKER_DELETE_TIMEOUT_SECS)" -ge 1 ] 2>/dev/null || { echo "LIMA_DOCKER_DELETE_TIMEOUT_SECS must be a positive integer"; exit 2; }
	@[ "$(LIMA_DOCKER_DELETE_KILL_AFTER_SECS)" -ge 1 ] 2>/dev/null || { echo "LIMA_DOCKER_DELETE_KILL_AFTER_SECS must be a positive integer"; exit 2; }
	@case "$(LIMA_INSTANCE)" in \
		""|*[!A-Za-z0-9._-]*|.|..) echo "Refusing invalid Lima instance name: $(LIMA_INSTANCE)"; exit 2;; \
		gludd-*) ;; \
		*) echo "Refusing non-Gludd Lima instance: $(LIMA_INSTANCE)"; exit 2;; \
	esac; \
	if [ "$(LIMA_DOCKER_DELETE_VALIDATE_ONLY)" = "1" ]; then \
		echo "LIMA_DOCKER_DELETE_VALID instance=$(LIMA_INSTANCE) timeout_secs=$(LIMA_DOCKER_DELETE_TIMEOUT_SECS) kill_after_secs=$(LIMA_DOCKER_DELETE_KILL_AFTER_SECS)"; \
		exit 0; \
	fi; \
	if [ "$(LIMA_DOCKER_DELETE_CONFIRM)" != "$(LIMA_INSTANCE)" ]; then \
		echo "LIMA_DOCKER_DELETE_CONFIRM must exactly match LIMA_INSTANCE"; \
		exit 2; \
	fi; \
	record=$$(limactl list "$(LIMA_INSTANCE)" --format '{{.Name}}|{{.Status}}|{{.Dir}}' 2>/dev/null || true); \
	if [ -z "$$record" ]; then \
		echo "LIMA_DOCKER_DELETE_ALREADY_ABSENT instance=$(LIMA_INSTANCE)"; \
		exit 0; \
	fi; \
	name=$${record%%|*}; rest=$${record#*|}; status=$${rest%%|*}; instance_dir=$${rest#*|}; \
	if [ "$$name" != "$(LIMA_INSTANCE)" ] || [ "$$record" = "$$rest" ] || [ "$$rest" = "$$instance_dir" ] || [ -z "$$instance_dir" ]; then \
		echo "Refusing ambiguous Lima instance record: $$record"; \
		exit 2; \
	fi; \
	case "$$status" in \
		Stopped) ;; \
		Running) echo "Refusing to delete running Lima instance: $(LIMA_INSTANCE)"; exit 1;; \
		*) echo "Refusing to delete Lima instance $(LIMA_INSTANCE) in status $$status"; exit 1;; \
	esac; \
	before_kib=$$(du -sk "$$instance_dir" 2>/dev/null | awk '{print $$1}' || true); \
	before_kib=$${before_kib:-0}; \
	echo "LIMA_DOCKER_DELETE_BEGIN instance=$(LIMA_INSTANCE) timeout_secs=$(LIMA_DOCKER_DELETE_TIMEOUT_SECS) size_kib=$$before_kib"; \
	limactl delete "$(LIMA_INSTANCE)" & \
	delete_pid=$$!; \
	terminate_owned_delete() { \
		kill -TERM "$$delete_pid" 2>/dev/null || true; \
		grace_elapsed=0; \
		while kill -0 "$$delete_pid" 2>/dev/null && [ "$$grace_elapsed" -lt "$(LIMA_DOCKER_DELETE_KILL_AFTER_SECS)" ]; do \
			sleep 1; grace_elapsed=$$((grace_elapsed + 1)); \
		done; \
		if kill -0 "$$delete_pid" 2>/dev/null; then \
			echo "LIMA_DOCKER_DELETE_KILL instance=$(LIMA_INSTANCE) kill_after_secs=$(LIMA_DOCKER_DELETE_KILL_AFTER_SECS) signal=KILL"; \
			kill -KILL "$$delete_pid" 2>/dev/null || true; \
		fi; \
		wait "$$delete_pid" 2>/dev/null || true; \
	}; \
	trap 'terminate_owned_delete; exit 130' HUP INT TERM; \
	delete_rc=0; elapsed=0; timed_out=0; \
	while kill -0 "$$delete_pid" 2>/dev/null; do \
		if [ "$$elapsed" -ge "$(LIMA_DOCKER_DELETE_TIMEOUT_SECS)" ]; then \
			echo "LIMA_DOCKER_DELETE_TIMEOUT instance=$(LIMA_INSTANCE) timeout_secs=$(LIMA_DOCKER_DELETE_TIMEOUT_SECS) signal=TERM"; \
			terminate_owned_delete; delete_rc=124; timed_out=1; break; \
		fi; \
		sleep 1; elapsed=$$((elapsed + 1)); \
		if [ $$((elapsed % 5)) -eq 0 ]; then echo "LIMA_DOCKER_DELETE_HEARTBEAT instance=$(LIMA_INSTANCE) elapsed_secs=$$elapsed"; fi; \
	done; \
	if [ "$$timed_out" -eq 0 ]; then wait "$$delete_pid" || delete_rc=$$?; fi; \
	trap - HUP INT TERM; \
	if [ "$$delete_rc" -ne 0 ]; then \
		echo "Lima Docker deletion failed or exceeded its bound: rc=$$delete_rc instance=$(LIMA_INSTANCE)"; \
		exit "$$delete_rc"; \
	fi; \
	after=$$(limactl list "$(LIMA_INSTANCE)" --format '{{.Name}}|{{.Status}}' 2>/dev/null || true); \
	if [ -n "$$after" ]; then \
		echo "Lima Docker deletion was not proven: instance=$(LIMA_INSTANCE) observed=$$after"; \
		exit 1; \
	fi; \
	echo "LIMA_DOCKER_DELETE_READY instance=$(LIMA_INSTANCE) status=Absent reclaimed_kib=$$before_kib"

lima-docker-status: ## Show bounded Docker engine, container, and image state for the namespaced Lima VM
	@case "$(LIMA_DOCKER_VALIDATE_ONLY)" in 0|1) ;; *) echo "LIMA_DOCKER_VALIDATE_ONLY must be 0 or 1"; exit 2;; esac
	@if [ "$(LIMA_DOCKER_VALIDATE_ONLY)" = "1" ]; then \
		echo "LIMA_DOCKER_STATUS_VALID instance=$(LIMA_INSTANCE) config=$(LIMA_DOCKER_CONFIG)"; \
		exit 0; \
	fi; \
	socket=$$(limactl list "$(LIMA_INSTANCE)" --format '{{.Dir}}/sock/docker.sock' 2>/dev/null || true); \
	if [ -z "$$socket" ] || [ ! -S "$$socket" ]; then \
		echo "Lima Docker socket unavailable for $(LIMA_INSTANCE): $$socket"; \
		exit 1; \
	fi; \
	mkdir -p "$(LIMA_DOCKER_CONFIG)"; \
	chmod 700 "$(LIMA_DOCKER_CONFIG)"; \
	echo "=== Lima Docker engine ($(LIMA_INSTANCE)) ==="; \
	DOCKER_CONFIG="$(LIMA_DOCKER_CONFIG)" DOCKER_HOST="unix://$$socket" docker info --format 'server={{.ServerVersion}} containers={{.Containers}} images={{.Images}}'; \
	echo "=== Containers ==="; \
	DOCKER_CONFIG="$(LIMA_DOCKER_CONFIG)" DOCKER_HOST="unix://$$socket" docker ps --all --no-trunc; \
	echo "=== Images ==="; \
	DOCKER_CONFIG="$(LIMA_DOCKER_CONFIG)" DOCKER_HOST="unix://$$socket" docker images --digests

lima-docker-pull: ## Pull one image through the namespaced Lima Docker socket with live progress
	@case "$(LIMA_DOCKER_VALIDATE_ONLY)" in 0|1) ;; *) echo "LIMA_DOCKER_VALIDATE_ONLY must be 0 or 1"; exit 2;; esac
	@if [ "$(LIMA_DOCKER_VALIDATE_ONLY)" = "1" ]; then \
		echo "LIMA_DOCKER_PULL_VALID instance=$(LIMA_INSTANCE) image=$(LIMA_IMAGE) config=$(LIMA_DOCKER_CONFIG)"; \
		exit 0; \
	fi; \
	socket=$$(limactl list "$(LIMA_INSTANCE)" --format '{{.Dir}}/sock/docker.sock' 2>/dev/null || true); \
	if [ -z "$$socket" ] || [ ! -S "$$socket" ]; then \
		echo "Lima Docker socket unavailable for $(LIMA_INSTANCE): $$socket"; \
		exit 1; \
	fi; \
	mkdir -p "$(LIMA_DOCKER_CONFIG)"; \
	chmod 700 "$(LIMA_DOCKER_CONFIG)"; \
	echo "Pulling $(LIMA_IMAGE) into Lima VM $(LIMA_INSTANCE)"; \
	DOCKER_CONFIG="$(LIMA_DOCKER_CONFIG)" DOCKER_HOST="unix://$$socket" docker pull "$(LIMA_IMAGE)"

.PHONY: podman-legacy-default-delete
podman-legacy-default-delete: ## Remove only the stopped legacy global Podman VM after namespaced migration
	@case "$(PODMAN_LEGACY_DELETE_VALIDATE_ONLY)" in 0|1) ;; *) echo "PODMAN_LEGACY_DELETE_VALIDATE_ONLY must be 0 or 1"; exit 2;; esac
	@[ "$(PODMAN_LEGACY_DELETE_TIMEOUT_SECS)" -ge 1 ] 2>/dev/null || { echo "PODMAN_LEGACY_DELETE_TIMEOUT_SECS must be a positive integer"; exit 2; }
	@if [ "$(PODMAN_LEGACY_MACHINE)" != "podman-machine-default" ]; then \
		echo "Refusing non-legacy machine: $(PODMAN_LEGACY_MACHINE)"; \
		exit 2; \
	fi; \
	if [ "$(PODMAN_LEGACY_DELETE_VALIDATE_ONLY)" = "1" ]; then \
		echo "PODMAN_LEGACY_DELETE_VALID machine=$(PODMAN_LEGACY_MACHINE) timeout_secs=$(PODMAN_LEGACY_DELETE_TIMEOUT_SECS)"; \
		exit 0; \
	fi; \
	command -v podman >/dev/null 2>&1 || { echo "podman not installed"; exit 1; }; \
	state=$$(podman machine inspect "$(PODMAN_LEGACY_MACHINE)" --format '{{.State}}' 2>/dev/null || true); \
	if [ -z "$$state" ]; then \
		echo "PODMAN_LEGACY_DELETE_ALREADY_ABSENT machine=$(PODMAN_LEGACY_MACHINE)"; \
		exit 0; \
	fi; \
	case "$$state" in running|Running) echo "Refusing to remove running legacy machine: $(PODMAN_LEGACY_MACHINE)"; exit 1;; esac; \
	log="/tmp/gludd-podman-legacy-default-delete.log"; \
	rm -f "$$log"; \
	echo "PODMAN_LEGACY_DELETE_START machine=$(PODMAN_LEGACY_MACHINE) state=$$state"; \
	podman machine rm -f "$(PODMAN_LEGACY_MACHINE)" >"$$log" 2>&1 & \
	delete_pid=$$!; \
	trap 'kill -TERM '"$$delete_pid"' 2>/dev/null || true' INT TERM EXIT; \
	elapsed=0; \
	while kill -0 "$$delete_pid" 2>/dev/null; do \
		echo "PODMAN_LEGACY_DELETE_HEARTBEAT machine=$(PODMAN_LEGACY_MACHINE) elapsed_secs=$$elapsed"; \
		if [ "$$elapsed" -ge "$(PODMAN_LEGACY_DELETE_TIMEOUT_SECS)" ]; then \
			kill -TERM "$$delete_pid" 2>/dev/null || true; \
			wait "$$delete_pid" 2>/dev/null || true; \
			[ ! -f "$$log" ] || cat "$$log"; \
			trap - INT TERM EXIT; \
			echo "PODMAN_LEGACY_DELETE_TIMEOUT machine=$(PODMAN_LEGACY_MACHINE) elapsed_secs=$$elapsed"; \
			exit 1; \
		fi; \
		sleep 1; \
		elapsed=$$((elapsed + 1)); \
	done; \
	wait "$$delete_pid"; delete_rc=$$?; \
	[ ! -f "$$log" ] || cat "$$log"; \
	rm -f "$$log"; \
	trap - INT TERM EXIT; \
	if [ "$$delete_rc" -ne 0 ]; then \
		echo "PODMAN_LEGACY_DELETE_FAILED machine=$(PODMAN_LEGACY_MACHINE) exit=$$delete_rc"; \
		exit "$$delete_rc"; \
	fi; \
	echo "PODMAN_LEGACY_DELETE_DONE machine=$(PODMAN_LEGACY_MACHINE) elapsed_secs=$$elapsed"

# Ensure the podman Linux VM is initialised and running (macOS needs a VM to run
# Linux containers). Idempotent: init/start fail harmlessly if already done.
podman-up:
	@command -v podman >/dev/null 2>&1 || { echo "podman not installed"; exit 1; }
	@if ! podman machine inspect "$(PODMAN_MACHINE)" >/dev/null 2>&1; then \
		echo "Initializing namespaced Podman machine $(PODMAN_MACHINE)"; \
		podman machine init --memory "$(VMEM)" --cpus "$(VCPU)" --disk-size "$(VDISK)" "$(PODMAN_MACHINE)"; \
	fi
	@state=$$(podman machine inspect "$(PODMAN_MACHINE)" --format '{{.State}}'); \
	if [ "$$state" != "running" ]; then podman machine start "$(PODMAN_MACHINE)"; fi
	@state=$$(podman machine inspect "$(PODMAN_MACHINE)" --format '{{.State}}'); \
	if [ "$$state" != "running" ]; then echo "Podman machine $(PODMAN_MACHINE) failed to start"; exit 1; fi
	@podman system connection default "$(PODMAN_MACHINE)"
	@podman machine list

# Start one explicit project-owned Podman machine and fail closed until its API
# is ready. Validate-only mode lets the target contract run without host changes.
.PHONY: podman-project-up
podman-project-up:
	@[ -n "$(PODMAN_MACHINE)" ] || { echo "Usage: make podman-project-up PODMAN_MACHINE=name PODMAN_START_TIMEOUT_SECS=30 PODMAN_VALIDATE_ONLY=0"; exit 2; }
	@case "$(PODMAN_MACHINE)" in *[!A-Za-z0-9_.-]*) echo "PODMAN_MACHINE contains unsupported characters"; exit 2;; esac
	@case "$(PODMAN_VALIDATE_ONLY)" in 0|1) ;; *) echo "PODMAN_VALIDATE_ONLY must be 0 or 1"; exit 2;; esac
	@[ "$(PODMAN_START_TIMEOUT_SECS)" -ge 1 ] 2>/dev/null || { echo "PODMAN_START_TIMEOUT_SECS must be a positive integer"; exit 2; }
	@if [ "$(PODMAN_VALIDATE_ONLY)" = "1" ]; then \
		echo "PODMAN_PROJECT_UP_VALID machine=$(PODMAN_MACHINE)"; \
		exit 0; \
	fi; \
	command -v podman >/dev/null 2>&1 || { echo "podman not installed"; exit 1; }; \
	recover_machine() { \
		echo "PODMAN_PROJECT_UP_RECOVER machine=$(PODMAN_MACHINE)"; \
		recovery_log="/tmp/gludd-podman-stop-$(PODMAN_MACHINE).log"; \
		rm -f "$$recovery_log"; \
		podman machine stop "$(PODMAN_MACHINE)" >"$$recovery_log" 2>&1 & \
		recovery_pid=$$!; \
		recovery_attempt=0; \
		while kill -0 "$$recovery_pid" 2>/dev/null; do \
			recovery_attempt=$$((recovery_attempt + 1)); \
			echo "PODMAN_PROJECT_UP_RECOVER_WAIT machine=$(PODMAN_MACHINE) attempt=$$recovery_attempt"; \
			if [ "$$recovery_attempt" -ge 2 ]; then \
				kill -TERM "$$recovery_pid" 2>/dev/null || true; \
				sleep 1; \
				kill -KILL "$$recovery_pid" 2>/dev/null || true; \
				break; \
			fi; \
			sleep 1; \
		done; \
		wait "$$recovery_pid" 2>/dev/null || true; \
		[ ! -f "$$recovery_log" ] || cat "$$recovery_log"; \
	}; \
	echo "PODMAN_PROJECT_UP_START machine=$(PODMAN_MACHINE)"; \
	podman system connection default "$(PODMAN_MACHINE)"; \
	log="/tmp/gludd-podman-start-$(PODMAN_MACHINE).log"; \
	rm -f "$$log"; \
	podman machine start "$(PODMAN_MACHINE)" >"$$log" 2>&1 & \
	start_pid=$$!; \
	trap 'kill -TERM '"$$start_pid"' 2>/dev/null || true' INT TERM EXIT; \
	attempt=0; \
	while kill -0 "$$start_pid" 2>/dev/null; do \
		attempt=$$((attempt + 1)); \
		echo "PODMAN_PROJECT_UP_START_WAIT machine=$(PODMAN_MACHINE) attempt=$$attempt"; \
		if [ "$$attempt" -ge "$(PODMAN_START_TIMEOUT_SECS)" ]; then \
			kill -TERM "$$start_pid" 2>/dev/null || true; \
			sleep 1; \
			kill -KILL "$$start_pid" 2>/dev/null || true; \
			wait "$$start_pid" 2>/dev/null || true; \
			[ ! -f "$$log" ] || cat "$$log"; \
			recover_machine; \
			trap - INT TERM EXIT; \
			echo "PODMAN_PROJECT_UP_TIMEOUT machine=$(PODMAN_MACHINE) attempts=$$attempt"; \
			exit 1; \
		fi; \
		sleep 1; \
	done; \
	wait "$$start_pid"; start_rc=$$?; \
	[ ! -f "$$log" ] || cat "$$log"; \
	trap - INT TERM EXIT; \
	if [ "$$start_rc" -ne 0 ] && ! podman info >/dev/null 2>&1; then \
		recover_machine; \
		echo "PODMAN_PROJECT_UP_START_FAILED machine=$(PODMAN_MACHINE) exit=$$start_rc"; \
		exit "$$start_rc"; \
	fi; \
	until podman info >/dev/null 2>&1; do \
		attempt=$$((attempt + 1)); \
		if [ "$$attempt" -ge "$(PODMAN_START_TIMEOUT_SECS)" ]; then \
			recover_machine; \
			echo "PODMAN_PROJECT_UP_TIMEOUT machine=$(PODMAN_MACHINE) attempts=$$attempt"; \
			exit 1; \
		fi; \
		echo "PODMAN_PROJECT_UP_WAIT machine=$(PODMAN_MACHINE) attempt=$$attempt"; \
		sleep 1; \
	done; \
	podman machine list; \
	echo "PODMAN_PROJECT_UP_READY machine=$(PODMAN_MACHINE)"

# Destructive recovery is restricted mechanically to the project namespace.
# Validate-only mode is the contract example and never touches a machine.
.PHONY: podman-project-recreate
podman-project-recreate:
	@[ -n "$(PODMAN_MACHINE)" ] || { echo "Usage: make podman-project-recreate PODMAN_MACHINE=gludd-e2e PODMAN_MEMORY_MB=4096 PODMAN_CPUS=4 PODMAN_DISK_GB=20 PODMAN_START_TIMEOUT_SECS=30 PODMAN_VALIDATE_ONLY=0"; exit 2; }
	@case "$(PODMAN_MACHINE)" in gludd|gludd-*) ;; *) echo "Refusing non-project Podman machine: $(PODMAN_MACHINE)"; exit 2;; esac
	@case "$(PODMAN_VALIDATE_ONLY)" in 0|1) ;; *) echo "PODMAN_VALIDATE_ONLY must be 0 or 1"; exit 2;; esac
	@[ "$(PODMAN_MEMORY_MB)" -ge 2048 ] 2>/dev/null || { echo "PODMAN_MEMORY_MB must be an integer >= 2048"; exit 2; }
	@[ "$(PODMAN_CPUS)" -ge 1 ] 2>/dev/null || { echo "PODMAN_CPUS must be a positive integer"; exit 2; }
	@[ "$(PODMAN_DISK_GB)" -ge 10 ] 2>/dev/null || { echo "PODMAN_DISK_GB must be an integer >= 10"; exit 2; }
	@[ "$(PODMAN_START_TIMEOUT_SECS)" -ge 1 ] 2>/dev/null || { echo "PODMAN_START_TIMEOUT_SECS must be a positive integer"; exit 2; }
	@if [ "$(PODMAN_VALIDATE_ONLY)" = "1" ]; then \
		echo "PODMAN_PROJECT_RECREATE_VALID machine=$(PODMAN_MACHINE)"; \
		exit 0; \
	fi; \
	command -v podman >/dev/null 2>&1 || { echo "podman not installed"; exit 1; }; \
	echo "PODMAN_PROJECT_RECREATE_STOP machine=$(PODMAN_MACHINE)"; \
	podman machine stop "$(PODMAN_MACHINE)" 2>&1 || true; \
	echo "PODMAN_PROJECT_RECREATE_REMOVE machine=$(PODMAN_MACHINE)"; \
	podman machine rm -f "$(PODMAN_MACHINE)" 2>&1 || true; \
	echo "PODMAN_PROJECT_RECREATE_INIT machine=$(PODMAN_MACHINE) memory_mb=$(PODMAN_MEMORY_MB) cpus=$(PODMAN_CPUS) disk_gb=$(PODMAN_DISK_GB)"; \
	podman machine init --memory "$(PODMAN_MEMORY_MB)" --cpus "$(PODMAN_CPUS)" --disk-size "$(PODMAN_DISK_GB)" "$(PODMAN_MACHINE)"; \
	echo "PODMAN_PROJECT_RECREATE_INITIALIZED machine=$(PODMAN_MACHINE)"
	@if [ "$(PODMAN_VALIDATE_ONLY)" != "1" ]; then \
		$(MAKE) --no-print-directory podman-project-up PODMAN_MACHINE="$(PODMAN_MACHINE)" PODMAN_START_TIMEOUT_SECS="$(PODMAN_START_TIMEOUT_SECS)" PODMAN_VALIDATE_ONLY=0; \
	fi

# Reclaim a project-owned Podman VM after live acceptance.  The namespace check
# prevents touching another project's machine, while the bounded background
# removal makes a slow VM teardown visible and prevents an unseen hang.
.PHONY: podman-project-delete
podman-project-delete:
	@[ -n "$(PODMAN_MACHINE)" ] || { echo "Usage: make podman-project-delete PODMAN_MACHINE=gludd-e2e PODMAN_DELETE_TIMEOUT_SECS=120 PODMAN_VALIDATE_ONLY=0"; exit 2; }
	@case "$(PODMAN_MACHINE)" in gludd|gludd-*) ;; *) echo "Refusing non-project Podman machine: $(PODMAN_MACHINE)"; exit 2;; esac
	@case "$(PODMAN_VALIDATE_ONLY)" in 0|1) ;; *) echo "PODMAN_VALIDATE_ONLY must be 0 or 1"; exit 2;; esac
	@[ "$(PODMAN_DELETE_TIMEOUT_SECS)" -ge 1 ] 2>/dev/null || { echo "PODMAN_DELETE_TIMEOUT_SECS must be a positive integer"; exit 2; }
	@if [ "$(PODMAN_VALIDATE_ONLY)" = "1" ]; then \
		echo "PODMAN_PROJECT_DELETE_VALID machine=$(PODMAN_MACHINE)"; \
		exit 0; \
	fi; \
	command -v podman >/dev/null 2>&1 || { echo "podman not installed"; exit 1; }; \
	if ! podman machine inspect "$(PODMAN_MACHINE)" >/dev/null 2>&1; then \
		echo "PODMAN_PROJECT_DELETE_ALREADY_ABSENT machine=$(PODMAN_MACHINE)"; \
		exit 0; \
	fi; \
	log="/tmp/gludd-podman-delete-$(PODMAN_MACHINE).log"; \
	rm -f "$$log"; \
	echo "PODMAN_PROJECT_DELETE_START machine=$(PODMAN_MACHINE) timeout_secs=$(PODMAN_DELETE_TIMEOUT_SECS)"; \
	podman machine rm -f "$(PODMAN_MACHINE)" >"$$log" 2>&1 & \
	delete_pid=$$!; \
	trap 'kill -TERM '"$$delete_pid"' 2>/dev/null || true' INT TERM EXIT; \
	elapsed=0; \
	while kill -0 "$$delete_pid" 2>/dev/null; do \
		echo "PODMAN_PROJECT_DELETE_HEARTBEAT machine=$(PODMAN_MACHINE) elapsed_secs=$$elapsed"; \
		if [ "$$elapsed" -ge "$(PODMAN_DELETE_TIMEOUT_SECS)" ]; then \
			kill -TERM "$$delete_pid" 2>/dev/null || true; \
			sleep 1; \
			kill -KILL "$$delete_pid" 2>/dev/null || true; \
			wait "$$delete_pid" 2>/dev/null || true; \
			[ ! -f "$$log" ] || cat "$$log"; \
			trap - INT TERM EXIT; \
			echo "PODMAN_PROJECT_DELETE_TIMEOUT machine=$(PODMAN_MACHINE) elapsed_secs=$$elapsed"; \
			exit 1; \
		fi; \
		sleep 1; \
		elapsed=$$((elapsed + 1)); \
	done; \
	wait "$$delete_pid"; delete_rc=$$?; \
	[ ! -f "$$log" ] || cat "$$log"; \
	rm -f "$$log"; \
	trap - INT TERM EXIT; \
	if [ "$$delete_rc" -ne 0 ]; then \
		echo "PODMAN_PROJECT_DELETE_FAILED machine=$(PODMAN_MACHINE) exit=$$delete_rc"; \
		exit "$$delete_rc"; \
	fi; \
	echo "PODMAN_PROJECT_DELETE_DONE machine=$(PODMAN_MACHINE) elapsed_secs=$$elapsed"

podman-restart:
	@podman machine stop 2>/dev/null || true
	@podman machine start
	@podman system connection default podman-machine-default 2>/dev/null || true
	@podman machine list

VMEM ?= 4096
VCPU ?= 4
# Recreate the podman VM from scratch with the requested resources. The default
# 2GiB VM crashed (and self-destructed) under the full test suite; a fresh VM
# with more memory is more reliable than trying to resize a dead one.
podman-resize:
	@podman machine rm -f podman-machine-default 2>/dev/null || true
	@podman machine init --memory $(VMEM) --cpus $(VCPU) 2>/dev/null || true
	@podman machine start
	@podman system connection default podman-machine-default 2>/dev/null || true
	@podman machine list

podman-diag:
	@echo "--- machine list ---"; podman machine list 2>&1 || true
	@echo "--- connection list ---"; podman system connection list 2>&1 || true
	@echo "--- info (host socket) ---"; podman info --format '{{.Host.RemoteSocket.Path}}' 2>&1 | head -3 || true

PYV ?= 3.11
ci-repro-linux:
	@if [ -z "$(CONTAINER_RUNTIME)" ]; then echo "ERROR: no docker/podman found — cannot reproduce the Linux gate locally"; exit 1; fi
	@echo "=== Reproducing CI Linux gate (python $(PYV)) via $(CONTAINER_RUNTIME) ==="
	@# slim base: tiny pull (avoids the I/O error unpacking the full image's giant
	@# static libs); uv downloads its own python like CI's setup-uv, so the base
	@# python barely matters. rc file preserves the container's real exit through
	@# the tee pipe (observability invariant: the pipe must not swallow failures).
	@rm -f /tmp/gludd-ci-repro-rc; \
	( $(CONTAINER_RUNTIME) run --rm -v "$$(pwd)":/work -w /work \
		-e UV_PROJECT_ENVIRONMENT=/opt/venv-linux -e GLUDD_AUTH_PSK="" \
		python:$(PYV)-slim-bookworm bash -c "set -e; \
			echo '--- installing make/lsof/curl/git + uv ---'; \
			apt-get update -qq && apt-get install -y -qq make lsof curl procps git >/dev/null; \
			pip install -q uv; \
			echo '--- locked profile sync (python $(PYV), container-local venv) ---'; \
			python scripts/dependency_profiles.py sync --root /work --set ci --environment /opt/venv-linux --python $(PYV); \
			export UV_NO_SYNC=1; \
			echo '--- running CI gate command ---'; \
			make lint typecheck test-count test smoke"; echo $$? > /tmp/gludd-ci-repro-rc ) 2>&1 | tee /tmp/gludd-ci-repro-$(PYV).log; \
	RC=$$(cat /tmp/gludd-ci-repro-rc 2>/dev/null || echo 1); \
	echo "=== ci-repro-linux exit=$$RC ==="; exit $$RC

SAST_REPORT ?= dist/sast-report.json
SAST_SUMMARY ?= dist/sast-summary.json
SAST_BASELINE ?=

sast:
	@mkdir -p "$$(dirname "$(SAST_REPORT)")"
	@$(UV) run bandit -q --ignore-nosec -r src/ -f json -o "$(SAST_REPORT)" || true
	@$(MAKE) --no-print-directory sast-summary SAST_REPORT="$(SAST_REPORT)" SAST_SUMMARY="$(SAST_SUMMARY)" SAST_BASELINE="$(SAST_BASELINE)"

sast-summary:
	@$(PYTHON) scripts/summarize_sast.py --report "$(SAST_REPORT)" --output "$(SAST_SUMMARY)" --baseline "$(SAST_BASELINE)"

sbom:
	@$(MAKE) --no-print-directory sync \
		DEPENDENCY_PROFILE_SET=sbom \
		DEPENDENCY_PROFILE_ENVIRONMENT=.venv-sbom \
		DEPENDENCY_PROFILE_PYTHON=3.11 \
		DEPENDENCY_PROFILE_VALIDATE_ONLY=0
	@mkdir -p dist
	@.venv-sbom/bin/cyclonedx-py environment .venv-sbom -o dist/sbom.json --of JSON

# Informational full audit of every committed audit-runtime lock (never gates).
pip-audit:
	-@UV_NO_SYNC=0 $(UV) run --no-project --python 3.11 python scripts/dependency_profiles.py audit --set audit-runtime \
		--root "$(CURDIR)" --manifest config/dependency_profiles.toml --uv "$(UV)"

# Gating audit (W5.3): fail-closed on any NEW advisory. The two
# non-exploitable advisories below are pinned by executable regression guards
# and documented in docs/SECURITY.md "Known dependency advisories":
#   - CVE-2025-69872 (diskcache): every constructor is forced through the
#     MessagePack-only safe adapter; legacy pickle modes never deserialize.
#   - PYSEC-2026-3552 (cryptography): the vulnerable PKCS#7 decrypt APIs are not
#     used anywhere in production source and a structural test fails on adoption.
# Fixed pip and ansible-core advisories are deliberately not ignored.
pip-audit-gate:
	@echo "=== locked profile audit (gating, W5.3) — fails on NEW advisories ==="
	@UV_NO_SYNC=0 $(UV) run --no-project --python 3.11 python scripts/dependency_profiles.py audit --set audit-runtime \
		--root "$(CURDIR)" --manifest config/dependency_profiles.toml --uv "$(UV)"
	@echo "=== pip-audit-gate: no un-adjudicated advisories ==="

pip-upgrade:
	@$(UV) pip install --reinstall 'pip>=26.1.2'
	@$(UV) run python -m pip --version

# Landed-guard regression gate for the D-07..D-30 security backlog: static
# probes on D-14/D-18/D-27 fail closed if their guard is silently removed;
# every other item is an honest OPEN ledger entry (never fails the gate).
security-backlog-gate:
	@$(UV) run python -m general_ludd.security.security_backlog

# Strict variant: fails on any OPEN item unless EXPECT_OPEN matches the
# actual count.  EXPECT_OPEN=0 is the acceptance gate for SEC-SBX-001:
# the feature SHALL remain Proposed until no controls are open.
# EXPECT_OPEN=<current-count> is the ratchet baseline — the count must
# never increase.
SECURITY_BACKLOG_STRICT_EXPECT_OPEN ?= 0
security-backlog-strict:
	@OPEN=$$($(UV) run python -m general_ludd.security.security_backlog 2>&1 \
		| grep '^TOTAL=' | sed 's/.*OPEN=\([0-9]*\).*/\1/'); \
	EXPECT="$(SECURITY_BACKLOG_STRICT_EXPECT_OPEN)"; \
	echo "security-backlog-strict: OPEN=$$OPEN EXPECT_OPEN=$$EXPECT"; \
	if [ "$$OPEN" -gt "$$EXPECT" ]; then \
		echo "FAIL — $$OPEN open items > expected $$EXPECT (ratchet or completion gate violated)"; \
		exit 1; \
	elif [ "$$OPEN" -lt "$$EXPECT" ]; then \
		echo "PASS — open items ($$OPEN) decreased below expected ($$EXPECT); update EXPECT_OPEN to ratchet down"; \
		exit 0; \
	else \
		echo "PASS — open items match expected count ($$EXPECT)"; \
		exit 0; \
	fi

# sast-gate: ratchet-based SAST gate.  Bandit scans src/ and writes the
# JSON report; the summarizer provides severity counts.  The gate fails
# when any severity class exceeds its configured ceiling, not when
# findings merely exist (the old target masked bandit's exit code with
# || true).  MAX_UNADJUDICATED_LOW is the per-category count ceiling;
# every low must be fixed or time-bounded and test-backed before the
# feature gate passes with MAX_LOW=0.
SAST_GATE_MAX_HIGH ?= 0
SAST_GATE_MAX_MEDIUM ?= 0
SAST_GATE_MAX_LOW ?= 506
sast-gate:
	@mkdir -p "$$(dirname "$(SAST_REPORT)")"
	@$(UV) run bandit -q --ignore-nosec -r src/ -f json -o "$(SAST_REPORT)" || true
	@$(PYTHON) scripts/summarize_sast.py --report "$(SAST_REPORT)" \
		--output "$(SAST_SUMMARY)" --baseline "$(SAST_BASELINE)" 2>/dev/null
	@HIGH=$$($(PYTHON) -c "import json; d=json.load(open('$(SAST_SUMMARY)')); h=d.get('by_severity',{}).get('HIGH',0); print(h if isinstance(h,int) else len(h))" 2>/dev/null || echo 0); \
	MEDIUM=$$($(PYTHON) -c "import json; d=json.load(open('$(SAST_SUMMARY)')); m=d.get('by_severity',{}).get('MEDIUM',0); print(m if isinstance(m,int) else len(m))" 2>/dev/null || echo 0); \
	LOW=$$($(PYTHON) -c "import json; d=json.load(open('$(SAST_SUMMARY)')); l=d.get('by_severity',{}).get('LOW',0); print(l if isinstance(l,int) else len(l))" 2>/dev/null || echo 0); \
	FAILED=0; \
	if [ "$$HIGH" -gt "$(SAST_GATE_MAX_HIGH)" ]; then echo "sast-gate: FAIL — HIGH=$$HIGH > max $(SAST_GATE_MAX_HIGH)"; FAILED=1; fi; \
	if [ "$$MEDIUM" -gt "$(SAST_GATE_MAX_MEDIUM)" ]; then echo "sast-gate: FAIL — MEDIUM=$$MEDIUM > max $(SAST_GATE_MAX_MEDIUM)"; FAILED=1; fi; \
	if [ "$$LOW" -gt "$(SAST_GATE_MAX_LOW)" ]; then echo "sast-gate: FAIL — LOW=$$LOW > max $(SAST_GATE_MAX_LOW)"; FAILED=1; fi; \
	if [ "$$FAILED" -eq 0 ]; then echo "sast-gate: PASS (HIGH=$$HIGH MEDIUM=$$MEDIUM LOW=$$LOW)"; else exit 1; fi

security: sast sbom pip-audit node-deps-audit security-backlog-gate

ci-precheck:
	@$(UV) run python scripts/ci_precheck.py

qa: lint typecheck test healthcheck
	@echo "QA gate passed."

validate: lint ansible-syntax healthcheck check-plugin-liveness
	@ERRS=$$($(UV) run mypy -p general_ludd 2>&1 | grep -c 'error:'); ERRS=$${ERRS:-0}; \
	if [ "$$ERRS" -le "$(MYPY_MAX)" ]; then echo "typecheck: OK ($$ERRS errors, baseline $(MYPY_MAX))"; else echo "typecheck: FAIL ($$ERRS errors > baseline $(MYPY_MAX))"; exit 1; fi
	@$(UV) run python -m pytest tests/ $(_XD) -q > /tmp/gludd-validate.txt 2>&1; EXIT=$$?; \
	if [ $$EXIT -eq 0 ]; then echo "test: PASS"; else echo "test: FAIL (non-zero exit)"; exit 1; fi
	@$(MAKE) --no-print-directory smoke > /dev/null 2>&1 && echo "smoke: PASS" || (echo "smoke: FAIL" && exit 1)
	@$(MAKE) --no-print-directory audit-evidence > /dev/null 2>&1 && echo "audit-evidence: PASS" || (echo "audit-evidence: FAIL" && exit 1)
	@echo "Full validation passed."

bootstrap: init lint test healthcheck
	@echo "Bootstrap complete."

db-sample-message:
	@sqlite3 $(OPENCODE_DB) "SELECT substr(m.data, 1, 500) FROM message m LIMIT 3;" 2>/dev/null

db-sample-part:
	@sqlite3 $(OPENCODE_DB) "SELECT substr(p.data, 1, 500) FROM part p LIMIT 3;" 2>/dev/null
	@sqlite3 $(OPENCODE_DB) ".schema" 2>/dev/null

db-tables:
	@sqlite3 $(OPENCODE_DB) ".tables" 2>/dev/null

# Recover a bounded set of completed Codex file-change events. Validation is
# the default; CODEX_REPLAY_APPLY=1 publishes only after the entire batch has
# replayed successfully in an isolated temporary tree.
replay-codex-file-changes:
	@[ -n "$(CODEX_REPLAY_DB)" ] && [ -n "$(CODEX_REPLAY_RECORDED_REPO)" ] && [ -n "$(CODEX_REPLAY_THREAD_ID)" ] && [ -n "$(CODEX_REPLAY_START)" ] && [ -n "$(CODEX_REPLAY_END)" ] || { echo "Usage: make replay-codex-file-changes CODEX_REPLAY_DB=path CODEX_REPLAY_RECORDED_REPO=path CODEX_REPLAY_THREAD_ID=uuid CODEX_REPLAY_START=n CODEX_REPLAY_END=n CODEX_REPLAY_APPLY=0|1"; exit 2; }
	@case "$(CODEX_REPLAY_APPLY)" in 0|1) ;; *) echo "CODEX_REPLAY_APPLY must be 0 or 1"; exit 2;; esac
	@case "$(CODEX_REPLAY_VALIDATE_ONLY)" in 0|1) ;; *) echo "CODEX_REPLAY_VALIDATE_ONLY must be 0 or 1"; exit 2;; esac
	@if [ "$(CODEX_REPLAY_VALIDATE_ONLY)" = "1" ]; then \
		test -f scripts/replay_codex_file_changes.py; \
		echo "CODEX_REPLAY_CONFIG_OK apply=$(CODEX_REPLAY_APPLY) range=$(CODEX_REPLAY_START)-$(CODEX_REPLAY_END)"; \
	else \
		$(UV) run python scripts/replay_codex_file_changes.py --database "$(CODEX_REPLAY_DB)" --repo . --recorded-repo "$(CODEX_REPLAY_RECORDED_REPO)" --thread-id "$(CODEX_REPLAY_THREAD_ID)" --start-ordinal "$(CODEX_REPLAY_START)" --end-ordinal "$(CODEX_REPLAY_END)" $(if $(filter 1,$(CODEX_REPLAY_APPLY)),--apply,); \
	fi

db-count:
	@sqlite3 $(OPENCODE_DB) "SELECT COUNT(*) FROM message;" 2>/dev/null

search-opencode:
	@sqlite3 $(OPENCODE_DB) "SELECT json_extract(m.data, '$$.role'), json_extract(p.data, '$$.text') FROM message m JOIN part p ON m.id = p.message_id WHERE json_extract(m.data, '$$.role')='user' AND json_extract(p.data, '$$.text') LIKE '%$(SEARCH)%' LIMIT $(MAX_RESULTS);" 2>/dev/null

collect-prompts:
	@echo "Collecting system prompts from open-source coding agents..."
	@$(UV) run python scripts/collect_prompts.py --output-dir config/prompt_profiles/collected
	@echo "Done. Run 'make collect-prompts SOURCE=aider' for a specific agent."

NAME ?= mp-diagnose

skill-list:
	@$(UV) run $(PYTHON) -c "from general_ludd.skills.catalog import SkillCatalog; cat = SkillCatalog(); [print(f'  {s.name:30s} {s.category:15s} {s.description[:60]}') for s in cat.search(limit=100)]"

skill-install:
	@$(UV) run $(PYTHON) -c "from general_ludd.skills.catalog import SkillCatalog; cat = SkillCatalog(); path = cat.install_skill('$(NAME)', '.opencode/skills'); print(f'Installed: {path}') if path else print(f'Skill not found: $(NAME)')"

bootstrap-skills:
	@echo "Installing default mattpocock skills..."
	@$(UV) run $(PYTHON) scripts/bootstrap_skills.py

list-tests:
	@find tests -name 'test_*.py' -type f | sort

# List every documented make target (lines matching `target-name:`), one per line.
# Excludes internal/helper targets starting with `_`. Subagents use this to discover
# available targets instead of guessing nonexistent ones.
list-targets:
	@$(PYTHON) -c "import re; targets = re.findall(r'^\s*(?!#)([a-zA-Z][-a-zA-Z0-9]*):', open('Makefile').read(), re.MULTILINE); [print(t) for t in sorted(set(targets)) if not t.startswith('_')]"

dogfood:
	@$(UV) run python scripts/dogfood.py

dogfood-features:
	@$(UV) run python scripts/dogfood_features.py
