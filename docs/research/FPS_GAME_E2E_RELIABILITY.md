# FPS game E2E reliability and reference-media policy

Status: implementation guidance for FPX.1 (2026-08-01). This document records
upstream operational evidence and defines what the live FPS acceptance must
prove. It does not claim that the acceptance is complete.

## Decision

The Azure FPS suite must provision at most one Gludd-owned inference endpoint
per test session, exercise every declared FPS fixture against that endpoint,
and destroy the endpoint once in a guaranteed finalizer. A caller-supplied
`AZURE_BASE_URL` is borrowed and must never be destroyed. A live-provision run
must fail, rather than skip, when a required media or game dependency is
missing.

Reference media is acquired before Azure spend and kept in a namespaced cache
outside the repository. Network acquisition is explicit and bounded to the
declared clip window. The actual Azure/game acceptance reads the cache only, so
YouTube availability, bot checks, throttling, and FFmpeg behavior cannot turn a
game regression into a network failure halfway through a paid run.

Every cached clip needs a sidecar provenance record containing:

- fixture name, immutable source video ID and canonical source URL;
- requested and decoded clip start/duration, upload/channel attribution, and
  the retrieval timestamp;
- license or redistribution decision (unknown means cache-only and never
  checked into Git);
- SHA-256 of the downloaded object plus the normalized decoded-frame digest;
- extractor and FFmpeg versions, selected format ID, container, video codec,
  pixel format, dimensions, frame rate, decoded frame count, and duration; and
- an approval/version field so a changed upstream transcode cannot silently
  replace the baseline.

A cache miss, digest mismatch, unavailable source, unsupported codec, zero
frames, or incomplete clip is a preflight failure. It is never a test skip and
must occur before Terraform starts.

The implemented operator boundary is:

```text
make game-reference-preflight \
  GAME_E2E_REFERENCE_NETWORK=1 \
  GAME_E2E_REFERENCE_CACHE_DIR=.cache/gludd-game-e2e \
  GAME_E2E_REFERENCE_VALIDATE_ONLY=0
```

Run it once with network access to acquire the bounded clips, then repeat with
`GAME_E2E_REFERENCE_NETWORK=0` before the paid acceptance. It emits
`reference_check_started`, `reference_acquisition_started`, sanitized yt-dlp
progress, `reference_ready`, and `reference_failed` as each event occurs. Each
ready event includes the cache status, frame count, and object digest. The Azure
runtime runs the same preflight callback before endpoint discovery or Terraform
deployment and emits `azure_game_preflight_failed` if it cannot proceed.

## Operator runbook: preflight, paid run, and cleanup

Use one explicit, namespaced cache for the whole run. Acquire reference media in
a non-paid preparation step, then prove that the exact cached objects work with
network access disabled:

```text
make game-reference-preflight \
  GAME_E2E_REFERENCE_NETWORK=1 \
  GAME_E2E_REFERENCE_CACHE_DIR=.cache/gludd-game-e2e \
  GAME_E2E_REFERENCE_VALIDATE_ONLY=0

make game-reference-preflight \
  GAME_E2E_REFERENCE_NETWORK=0 \
  GAME_E2E_REFERENCE_CACHE_DIR=.cache/gludd-game-e2e \
  GAME_E2E_REFERENCE_VALIDATE_ONLY=0
```

For each declared fixture, the command streams `reference_check_started`, then
either `reference_ready` or `reference_acquisition_started` followed by a
terminal ready/failed event. It reports all independently checkable fixtures
before returning nonzero, so an operator can begin repairing the first failure
without waiting for a later paid suite. A successful offline pass ends with
`GAME_REFERENCE_PREFLIGHT_OK`. `GAME_E2E_REFERENCE_VALIDATE_ONLY=1` validates
the command and combined Pygame/video import boundary only; it does not prove
that media exists, so it cannot replace the second command above.

Validate the credential pointer and timeout without provisioning. The
repository default is `/tmp/general-ludd.env`, not `/tmp/general-luddite.env`,
but the path is still supplied explicitly so the evidence is unambiguous:

```text
make test-e2e-games-provision \
  AZURE_E2E_ENV_FILE=/tmp/general-ludd.env \
  AZURE_E2E_VALIDATE_ONLY=1 \
  GAME_E2E_TIMEOUT_SECS=3600 \
  GAME_E2E_REFERENCE_NETWORK=0 \
  GAME_E2E_REFERENCE_CACHE_DIR=.cache/gludd-game-e2e \
  GLUDD_E2E_MAX_SPEND_USD=5
```

This validation-only mode proves file readability and argument shape; it does
not authenticate to Azure. Run the paid path only after both the configuration
check and offline media preflight pass:

```text
make test-e2e-games-provision \
  AZURE_E2E_ENV_FILE=/tmp/general-ludd.env \
  AZURE_E2E_VALIDATE_ONLY=0 \
  GAME_E2E_TIMEOUT_SECS=3600 \
  GAME_E2E_REFERENCE_NETWORK=0 \
  GAME_E2E_REFERENCE_CACHE_DIR=.cache/gludd-game-e2e \
  GLUDD_E2E_MAX_SPEND_USD=5
```

The paid log must show this order:

1. `azure_game_preflight_started`, per-fixture reference events, and
   `azure_game_preflight_completed`;
2. `azure_game_deploy_started` and `azure_game_deploy_completed`;
3. repeated `azure_game_readiness_probe` events and
   `azure_game_endpoint_ready`;
4. streamed fixture generation, controls, capture, and comparison output; and
5. `azure_game_destroy_started` followed by `azure_game_destroy_completed` for
   an endpoint owned by the test.

A media/runtime failure instead must show `reference_failed` and
`azure_game_preflight_failed`, exit nonzero, and never emit
`azure_game_deploy_started`. That absence is the paid-deploy safety invariant.
The wrapper flushes output to the console and to timestamped
`.gate-logs/e2e-azure/games-provision-*.log` and companion `.json` records, so
the first actionable failure survives even when later fixture setup fails or
the outer timeout terminates pytest.

`azure_game_destroy_completed` proves that Gludd's destroy call returned; it is
not, by itself, Azure absence evidence. After a paid or interrupted run, inspect
the subscription and then reconcile any E2E-owned groups to verified absence:

```text
make azure-cleanup-inspect \
  AZURE_E2E_ENV_FILE=/tmp/general-ludd.env \
  AZURE_CLI=az

make azure-cleanup-e2e \
  AZURE_E2E_ENV_FILE=/tmp/general-ludd.env \
  AZURE_CLEANUP_TIMEOUT_SECS=1800 \
  AZURE_CLEANUP_POLL_SECS=10 \
  AZURE_CLI=az
```

The cleanup target selects every resource group whose name starts with
`gludd-gpu`; never run it in a subscription containing another active Gludd E2E
run. Preserve `CLEANUP_SCAN`, each `CLEANUP_DELETE`, the live `CLEANUP_POLL`
heartbeats, and the terminal `CLEANUP_VERIFIED leaked_resources=0`. A timeout,
list failure, or delete failure is a failed acceptance, not a deferred success.
This distinction follows Azure's asynchronous resource-group deletion contract
([official ARM deletion guidance][azure-group-delete]) and the project's
[Container Apps cleanup runbook][azure-cleanup-runbook]. Operators have reported
20-plus-minute environment deletion ([pipeline timeout report][aca-timeout]) and
apparently empty environments blocked by hidden children
([hidden-child report][aca-hidden-child]); the independent inventory is therefore
part of the proof, rather than cleanup ceremony.

## Why live YouTube downloads are not the acceptance path

The yt-dlp maintainers' long-running [known-issues thread][ytdlp-known] records
IP/account bot checks, immediate `403` failures, throttling, format changes,
FFmpeg regressions, and inaccurate section cuts. A 2025 operator report shows
downloads repeatedly failing with `403` after only a few percent even on the
then-current stable release ([yt-dlp issue 14138][ytdlp-403]). Another report
documents `--download-sections` behavior changing with YouTube player clients
and warns that some streams commonly return `403` or EOF errors
([yt-dlp issue 15036][ytdlp-sections]). These are source-transport failures,
not evidence about Gludd or generated games.

The current clip acquisition uses `--download-sections` and forced keyframes,
which is a sensible bounded fetch, but source ID plus time range alone is not a
pin. The normalized-frame digest closes that gap while allowing the original
media to remain outside the repository.

## Headless controls are an explicit test interface

Setting `SDL_VIDEODRIVER=dummy` is appropriate for render capture, and SDL
requires the driver hint before initialization ([SDL driver hint][sdl-driver]).
It does not simulate a keyboard or mouse. Pygame's own headless guide warns
that device events and the key module are unavailable with the dummy driver
([Pygame headless guide][pygame-headless]). Pygame also documents that its
event queue depends on an initialized display, can silently drop events when
full, and must be drained every frame ([Pygame event API][pygame-events]). The
project's release history has even skipped surface tests that fail under the
dummy driver ([Pygame release evidence][pygame-release]).

Therefore a test must not post `KEYDOWN` and then assume
`pygame.key.get_pressed()` changed. Each generated fixture must expose a small,
deterministic test interface (or run under an equivalent injected adapter):

```text
create_game(seed) -> game
game.step(events, pressed_keys, mouse_delta, dt) -> state
game.render(surface) -> frame
```

The production loop may translate real Pygame input into that interface. The
test injects `RETURN`, every required movement/action control, mouse motion,
and `ESCAPE`, while asserting the reported state and captured frame after each
transition. A source-text search for key names is not proof that controls work.

## Per-fixture acceptance

For every `genre == "fps"` fixture, the live test must produce one structured
result and stream each stage immediately:

1. generation used the session's Azure endpoint and no hosted fallback;
2. generated code passed syntax, forbidden-operation, and explicit game
   contract validation;
3. initial state was `menu`, with a nonblank menu frame and all declared
   controls visible;
4. `RETURN` transitioned to gameplay;
5. each declared key and mouse input caused the expected state or measurable
   frame/camera change, without crashing or hanging;
6. at least the declared frame count was captured with stable dimensions,
   nonblank content, temporal motion, and bounded frame time;
7. the stage-aligned generated capture was compared with the fixture's pinned,
   provenance-verified online-reference clip; and
8. `ESCAPE` returned to the menu and cleanup left no game process, Azure app,
   managed environment, resource group, or cache writer running.

Raw same-index SSIM is retained as a diagnostic, not used alone as the fidelity
verdict: independently rendered gameplay will be shifted in space and time.
The verdict must combine a documented stage alignment with spatial and motion
metrics, publish all component scores, and use thresholds calibrated on known
positive and deliberately broken fixtures. No threshold may be lowered merely
to make generated output pass.

## SSIM scale and numerical invariants

The local game suite reproduced a backend failure on the macOS/Python 3.14
dependency set: scikit-image returned approximately zero for two identical
600x800 RGB frames and `1.0` for black versus white frames. The project now
tests those identity and high-error invariants directly instead of trusting a
third-party scalar solely because it is finite.

This also addresses a long-lived upstream accuracy concern. The scikit-image
maintainers' [large-image SSIM discussion][skimage-downsample] records that the
original SSIM preprocessing average-pools by
`max(1, round(min(height, width) / 256))`; the reporter measured substantially
better human-ranking correlation after that preprocessing. A separate operator
report found large disagreement between scikit-image and the original MATLAB
implementation ([scikit-image issue 4278][skimage-matlab]). Gludd therefore
applies the documented average-pooling factor before local SSIM, returns `1.0`
for byte-identical frames, and uses a stable global SSIM only when the backend
is non-finite or claims near-identity despite normalized pixel error of at
least 25%. This is a fail-closed sanity path, not a lower acceptance threshold.

The call still specifies the 8-bit `data_range=255` and RGB `channel_axis`, as
required by the [scikit-image metric contract][skimage-ssim-api].

## macOS SDL/OpenCV import collision is a blocking warning

The first paid-path dry failure exposed Objective-C duplicate-class warnings:
OpenCV's `cv2/.dylibs/libSDL2-2.0.0.dylib` and Homebrew SDL (loaded by the
Python 3.14 Pygame build) were present in one process. macOS states that which
duplicate class wins is undefined, so this is a correctness and crash risk, not
cosmetic log noise. Azure provisioning remains blocked until the combined game
runtime import is warning-free.

This failure family is long-lived in user reports. A Stack Overflow operator
reported the same undefined duplicate-class behavior between OpenCV-bundled and
application GUI libraries ([OpenCV/Qt duplicate report][opencv-qt-duplicate]);
another macOS report shows a bundled `.dylibs/libSDL2-2.0.0.dylib` causing a
runtime loader failure ([bundled SDL report][bundled-sdl-report]). A recent
Apple-Silicon game-runtime account reproduces the exact `SDLApplication`
collision between Homebrew SDL and `cv2/.dylibs` ([GRF SDL report][grf-sdl]).

The OpenCV wheel maintainers require exactly one OpenCV wheel and recommend the
headless variant when GUI APIs are unused ([OpenCV wheel guidance][opencv-wheel]).
Gludd already satisfies that package-selection rule, but an upstream video-I/O
discussion confirms binary wheels carry their own FFmpeg stack rather than the
system FFmpeg ([OpenCV video-I/O report][opencv-videoio]). Therefore merely
reinstalling the same headless wheel is not accepted as evidence; the gate must
exercise the actual Pygame-plus-video runtime and reject duplicate-class stderr.

The reproduced boundary was `opencv-python-headless==4.13.0.92`; OpenCV 4.13
added FFmpeg 8 support ([OpenCV 4.13 changelog][opencv-413]). Constraining both
game extras to `opencv-python-headless>=4.9.0,<4.13` resolved the warning with
4.12.0.88 on the same macOS/Python 3.14 process. The preflight now launches a
clean child that imports the actual `general_ludd.cloud.game_e2e` module, captures
stderr, and fails on duplicate-class markers. This executable check remains the
contract even if a future wheel changes its transitive binaries.

## Efficiency and failure ordering

The cheapest deterministic checks run first: environment shape, optional
dependencies, reference cache/digests, generated-code contract, then Azure
credentials and plan. Only after those pass may the shared endpoint be
provisioned. Generation results are cached per `(fixture, prompt version, model
identity, model revision, sampling configuration)` within the session so the
same game is not regenerated for separate assertions. Tests consume streamed
stage events and may begin independent analysis as soon as each fixture's
capture lands; teardown still runs once after the final fixture or first fatal
infrastructure failure.

[pygame-events]: https://www.pygame.org/docs/ref/event.html
[pygame-headless]: https://www.pygame.org/wiki/HeadlessNoWindowsNeeded
[pygame-release]: https://github.com/pygame/pygame/releases
[sdl-driver]: https://wiki.libsdl.org/SDL2/SDL_HINT_VIDEODRIVER
[skimage-downsample]: https://github.com/scikit-image/scikit-image/issues/5192
[skimage-matlab]: https://github.com/scikit-image/scikit-image/issues/4278
[skimage-ssim-api]: https://scikit-image.org/docs/stable/api/skimage.metrics.html#skimage.metrics.structural_similarity
[ytdlp-403]: https://github.com/yt-dlp/yt-dlp/issues/14138
[ytdlp-known]: https://github.com/yt-dlp/yt-dlp/issues/3766
[ytdlp-sections]: https://github.com/yt-dlp/yt-dlp/issues/15036
[bundled-sdl-report]: https://stackoverflow.com/questions/65909503/how-can-i-play-video-in-opencv-with-audio-the-same-time
[grf-sdl]: https://medium.com/@Nirodya_Pussadeniya/installing-google-research-football-grf-on-macos-apple-silicon-38887a485fc1
[opencv-qt-duplicate]: https://stackoverflow.com/questions/51371421/pyqt5-and-opencv-have-similar-libraries-how-to-avoid-conflict-between-the-2
[opencv-413]: https://github.com/opencv/opencv/wiki/OpenCV-Change-Logs#version4130
[opencv-videoio]: https://github.com/opencv/opencv/issues/24430
[opencv-wheel]: https://github.com/opencv/opencv-python
[aca-hidden-child]: https://learn.microsoft.com/en-us/answers/questions/1660320/cannot-delete-container-app-environment
[aca-timeout]: https://stackoverflow.com/questions/78004168/deleting-azure-container-app-takes-long-time-and-time-out-the-pipeline
[azure-cleanup-runbook]: AZURE_CONTAINER_APP_CLEANUP_LATENCY.md
[azure-group-delete]: https://learn.microsoft.com/en-us/azure/azure-resource-manager/management/delete-resource-group
