# Feature: Radio Engineer Collection

**Status: COMPLETE** | **Created: 2026-07-14** | **Completed: 2026-08-03** | **Target: v0.1.0-beta.2**

## 1. Overview

Ansible collection `general_ludd.radio` providing the agent RF/SDR capability.
Covers signal capture, digital decoding, antenna design, propagation modeling,
ham/marine exam data, frequency regulation lookup, and link budget calculation.

## 2. Roles (10)

| Role | Purpose | Key Backend |
|------|---------|-------------|
| `sdr_capture` | Capture IQ samples from SDR hardware, set freq/gain/sample-rate | pyrtlsdr, SoapySDR |
| `signal_identify` | Classify modulation type, baud rate, protocol from IQ data | scipy.signal, FFT |
| `decode_digital` | Decode DMR/P25/NXDN/D-STAR/APRS/FT8/WSJT-X | dsd, multimon-ng, direwolf |
| `antenna_design` | Design + simulate antenna for freq/polarization/gain | NEC2, openEMS, template equations |
| `propagation_model` | RF path loss (ITM Longley-Rice, Hata, free-space) | scipy, ITM Python ports |
| `exam_quiz` | Structured ham (FCC Tech/General/Extra) + marine (ROC-M, GMDSS) Q&A | radio_exam_data.py |
| `regulation_lookup` | Frequency allocation, license class, band plan by country | frequency_allocations.py |
| `marine_decode` | AIS, NAVTEX, DSC, EPIRB/SART decoding | rtl_ais, aislib |
| `link_budget` | TX power + antenna gain minus path loss = SNR margin | Template math, link_budget.py |
| `spectrum_scan` | Wideband sweep, waterfall analysis, peak detection | rtl_power, scipy.signal |

## 3. Knowledge Modules

| Module | Content |
|--------|---------|
| `radio_exam_data.py` | Structured Q&A: text, choices, correct, explanation, exam, section |
| `frequency_allocations.py` | Nested dict: country → band → {start, end, service, license_class} |
| `modulation_schemes.py` | Enum + properties: symbol rate, bandwidth, spectrum shape per mode |
| `antenna_types.py` | Design equations + radiation patterns for dipole, yagi, loop, patch, discone |
| `propagation_models.py` | ITM (Longley-Rice) port, Hata-Okumura, free-space, two-ray ground |

## 4. Implementation Plan

| Phase | Scope | Deliverables |
|-------|-------|-------------|
| P1 | Core SDR | sdr_capture, spectrum_scan, antenna_design + antenna_types.py + modulation_schemes.py |
| P2 | Digital modes + marine | decode_digital, marine_decode, signal_identify, propagation_model + propagation_models.py |
| P3 | Exam + regulatory | exam_quiz, regulation_lookup, link_budget + exam/frequency data modules |

## 5. Files

```text
collections/ansible_collections/general_ludd/radio/
├── galaxy.yml, README.md
├── plugins/module_utils/
│   ├── radio_exam_data.py
│   ├── frequency_allocations.py
│   ├── modulation_schemes.py
│   ├── antenna_types.py
│   └── propagation_models.py
└── roles/{sdr_capture,signal_identify,decode_digital,antenna_design,
           propagation_model,exam_quiz,regulation_lookup,marine_decode,
           link_budget,spectrum_scan}/{tasks,defaults}/main.yml + README.md
```

## 6. Dependencies

Python: `numpy`, `scipy` (signal), `pyrtlsdr`, `SoapySDR` (optional)
System CLI: `dsd`, `multimon-ng`, `direwolf`, `rtl_433`, `rtl_power`, `CSDR`
Optional: `GNU Radio`, `openEMS`, `WSJT-X`

Loose-coupling: if a system tool is absent, role returns `skipped` + `missing_tool`.

## 7. Test Plan

- Unit: each knowledge module has shape + key presence tests
- Role: 1 molecule scenario per role (artifact written, verdict field)
- Collection: `ansible-galaxy collection build` exits 0; `ansible-lint` passes
- Integration: end-to-end playbook invokes all 10 roles with check_mode
