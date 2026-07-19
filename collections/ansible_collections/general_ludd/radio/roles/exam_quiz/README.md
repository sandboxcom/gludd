# `general_ludd.radio.exam_quiz` — Ham & Marine Exam Quiz

Structured Q&A for FCC ham exams (Technician, General, Extra) and marine exams (ROC-M, GMDSS).

## Quick start

```yaml
- name: Quiz FCC Technician exam
  hosts: localhost
  vars:
    exam_quiz_enabled: true
    exam_quiz_exam: fcc_tech
    exam_quiz_count: 20
  roles:
    - general_ludd.radio.exam_quiz
```

## Variables

| Variable | Default | Description |
|---|---|---|
| `exam_quiz_enabled` | `false` | Enable quiz |
| `exam_quiz_exam` | `fcc_tech` | fcc_tech / fcc_general / fcc_extra / roc_m / gmdss |
| `exam_quiz_section` | `null` | Filter by section name |
| `exam_quiz_count` | `10` | Number of questions |
| `exam_quiz_random` | `true` | Shuffle questions |
