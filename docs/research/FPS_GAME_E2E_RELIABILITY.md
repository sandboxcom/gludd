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
