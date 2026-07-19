# `general_ludd.radio` — RF / SDR Agent Collection

Ansible collection providing agents with radio-frequency and software-defined-radio
capability.  Covers signal capture, digital-mode decoding, antenna design,
propagation modeling, ham / marine exam data, frequency regulation lookup, and
link budget calculation.

## Roles

| Role | Purpose |
|---|---|
| `sdr_capture` | Capture IQ samples from SDR hardware |
| `signal_identify` | Classify modulation type, baud rate, protocol |
| `decode_digital` | Decode DMR / P25 / NXDN / D-STAR / APRS / FT8 / WSJT-X |
| `antenna_design` | Design and simulate antenna for given parameters |
| `propagation_model` | RF path loss (ITM Longley-Rice, Hata, free-space) |
| `exam_quiz` | Structured ham + marine exam Q&A |
| `regulation_lookup` | Frequency allocation, license class, band plan by country |
| `marine_decode` | AIS, NAVTEX, DSC, EPIRB / SART decoding |
| `link_budget` | TX power + antenna gain - path loss = SNR margin |
| `spectrum_scan` | Wideband sweep, waterfall analysis, peak detection |

## Knowledge Modules

| Module | Content |
|---|---|
| `radio_exam_data.py` | Structured Q&A for ham (FCC Tech/General/Extra) + marine exams |
| `frequency_allocations.py` | Country → band → start/end/service/license_class |
| `modulation_schemes.py` | Enum + properties per modulation mode |
| `antenna_types.py` | Design equations + radiation patterns for common antenna types |
| `propagation_models.py` | ITM Longley-Rice port, Hata-Okumura, free-space, two-ray |

## Quick start

```yaml
- name: Capture SDR IQ samples
  hosts: localhost
  vars:
    sdr_capture_enabled: true
    sdr_capture_freq_hz: 162400000
    sdr_capture_sample_rate: 2048000
  roles:
    - general_ludd.radio.sdr_capture
```

## Dependencies

- Python: numpy, scipy (signal), pyrtlsdr, SoapySDR (optional)
- System CLI: dsd, multimon-ng, direwolf, rtl_power, CSDR
- Optional: GNU Radio, openEMS, WSJT-X

Loose-coupling: if a system tool is absent the role returns `skipped` + `missing_tool`.
