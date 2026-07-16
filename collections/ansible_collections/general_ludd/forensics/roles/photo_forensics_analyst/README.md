# `general_ludd.forensics.photo_forensics_analyst` -- Photo Forensics Analyzer

Run digital photo/video forensic analysis on an image file -- metadata extraction,
Error Level Analysis, clone/splice/resample detection, AI-generated detection, and
camera identification.

## Quick start

```yaml
- name: Run photo forensics
  hosts: localhost
  vars:
    photo_forensics_image_path: "/evidence/photos/suspect_image.jpg"
    photo_forensics_analysis_types:
      - metadata
      - ela
      - modification
      - ai_detection
      - camera_id
  roles:
    - general_ludd.forensics.photo_forensics_analyst
```

## Variables

| Variable | Default | Description |
|---|---|---|
| `photo_forensics_enabled` | `true` | Enable the role |
| `photo_forensics_image_path` | `""` | Path to image file to analyze |
| `photo_forensics_analysis_types` | `[metadata, ela, modification, ai_detection, camera_id]` | Which analyses to run |
| `photo_forensics_output_dir` | `{{ playbook_dir }}/output/forensics/photo` | Output directory |
