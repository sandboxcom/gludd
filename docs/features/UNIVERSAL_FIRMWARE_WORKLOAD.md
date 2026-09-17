# Universal Arduino-Class Firmware Workload

## Scope

Gludd treats firmware generation as a domain adapter over the universal task
runtime, not as a self-improvement feature. A model may propose a candidate,
but the candidate is not complete until deterministic tools compile, statically
analyze, and simulate it for the exact requested board.

`ArduinoFirmwareAdapter` implements the same `TaskAdapterProtocol` as
`PolymerDesignAdapter`. `UniversalTaskExecutor` therefore owns model-provider
selection, accelerator eligibility, budget enforcement, durable scheduler
admission, model invocation, and result status for both domains. The embedded
package owns only board qualification, firmware schema and source checks, and
the domain-specific acceptance evidence. Neither direction imports
`self_improve`; self-improvement is another possible consumer of this runtime.

`ArduinoToolRunner` is the narrow bridge from the universal allowlisted tool
surface to a `FirmwareToolchain`. It always runs one bounded pipeline in this
order: Arduino CLI compile, Cppcheck AVR analysis, then simavr. It stops on a
failed compile or static check and returns immutable evidence rather than
executing an arbitrary command supplied by a model.

The first supported board family is AVR Arduino:

| FQBN | MCU | Clock | Simulator |
| --- | --- | ---: | --- |
| `arduino:avr:uno` | `atmega328p` | 16 MHz | simavr |
| `arduino:avr:nano` | `atmega328p` | 16 MHz | simavr |
| `arduino:avr:mega` | `atmega2560` | 16 MHz | simavr |

An unknown FQBN is unsupported rather than guessed. Physical-device access is
off by default and refused by this workload; upload belongs in a separate,
explicitly approved deployment workflow.

## Completion Contract

A firmware task is complete only when all of these records are present and
valid:

1. The universal router supplies a healthy local or Azure endpoint decision
   with capability, privacy, cost, classification, accelerator, and health
   evidence. Restricted work is eligible only for an offline local target.
2. The model returns strict JSON containing complete Arduino C++ source for the
   exact FQBN. Markdown, prose, placeholders, board substitution, and unsafe
   host or upload operations are rejected.
3. Arduino CLI compiles the sketch with `--fqbn`; a SHA-256 digest identifies
   the resulting ELF artifact.
4. Cppcheck runs with its AVR model and returns no configured finding.
5. simavr runs the ELF for the board MCU and clock and emits the request's
   expected serial observable within a bounded run.
6. Provenance binds the source digest, compile artifact digest, route decision,
   board profile, command arguments, and tool versions.

The provider-neutral request uses capability `arduino-cpp`, allowlists only
`arduino_toolchain`, and carries `board_fqbn`, `expected_serial`, and the
default-false `physical_device_access` flag as typed metadata. Local Ollama,
local vLLM, Azure-hosted vLLM, and Azure model endpoints can all implement the
same injected gateway contract; no provider SDK appears in the embedded code.

Failure, missing tools, an uncompiled candidate, missing artifact digest, static
findings, simulator failure, or a missing observable always leaves
`completed=false`. Source text and routing scaffolds can never satisfy the
contract.

## Tool Selection and Practitioner Evidence

The implementation reuses mature tools rather than reproducing their parsers or
simulators:

- Arduino documents the FQBN as the board/compiler qualification and requires
  it for `arduino-cli compile`. Its project profiles can additionally pin cores
  and libraries for reproducible builds. See the
  [Arduino CLI getting-started guide](https://docs.arduino.cc/arduino-cli/getting-started)
  and [sketch build profiles](https://docs.arduino.cc/arduino-cli/sketch-project-file).
- Cppcheck is designed for non-standard embedded C/C++ and ships an AVR library
  model. Its maintainers explicitly warn that analysis is imperfect and that
  configuration affects false negatives. See the
  [Cppcheck manual](https://github.com/cppcheck-opensource/cppcheck/blob/main/man/manual.md).
- simavr is an offline AVR simulator that accepts ELF artifacts, models common
  peripherals, and supports serial/VCD evidence. See the
  [simavr project documentation](https://github.com/buserror/simavr).

Practitioner reports expose durable limitations that the Gludd contract keeps
visible:

- Arduino users have repeatedly encountered installed-core/FQBN mismatches,
  including a 2023 STM32 case where the apparent board identifier selected a
  different platform namespace. Gludd therefore never infers or silently
  changes an FQBN. See the
  [Arduino forum report](https://forum.arduino.cc/t/how-to-change-arduino-builder-to-arduino-cli-in-arduino-ide-1-8-19/1137513).
- simavr's host-to-UART interface has had an open usability request since 2016.
  The initial contract only validates bounded serial output; it does not claim
  interactive peripheral fidelity. See
  [simavr issue 157](https://github.com/buserror/simavr/issues/157).
- Practitioners have reported simulator-versus-hardware timing differences for
  USART/SPI since 2022. Simulation proves the declared logical observable, not
  electrical or real-time certification. See
  [simavr issue 477](https://github.com/buserror/simavr/issues/477).
- Wokwi offers CI simulation for additional boards, but requires a service token
  and users have reported headless timeout failures. It is therefore a future
  opt-in adapter, not an unrecorded fallback for the offline default. See the
  [Wokwi CI action](https://github.com/wokwi/wokwi-ci-action) and
  [timeout report](https://github.com/wokwi/wokwi-ci-action/issues/4).

These limitations must remain in the result provenance. A simulator pass does
not authorize a physical upload or certify analog behavior, timing, safety, or
regulatory compliance.

## Verification

`tests/unit/test_embedded_firmware_workload.py` exercises local and Azure route
evidence, strict model-output parsing, privacy and physical-access refusal,
compile/static/simulator fail-closed behavior, command construction, artifact
hashing, bounded simulator termination, and the package dependency boundary.
`tests/unit/test_universal_firmware_adapter.py` additionally executes the
firmware capability end-to-end through `UniversalTaskExecutor`, proves
evidence-based local/Azure selection with approved accelerators, exercises the
shared policy and scheduler gates, and rejects missing, malformed, unsafe, or
incomplete tool evidence. The polymer acceptance suite exercises the same
executor and adapter method surface, providing a cross-domain architectural
proof instead of a self-improvement surrogate.

Deployment is additive and supports ZDD: ship the adapter and tool bridge dark,
verify the board toolchain and provider targets, register `arduino-cpp` routing,
then shift new tasks to it while existing workers drain. Rollback removes that
registration; there is no schema migration or persistent firmware state to
reverse.
The focused branch-aware coverage configuration is
`config/coverage_universal_firmware.ini`.
