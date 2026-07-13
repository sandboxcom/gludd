# general_ludd.web

Ansible collection for web development and design research — HTML5/CSS3 authoring,
responsive design validation, JavaScript debugging and error analysis, and
design research via website analysis.

## Philosophy

The web is built on HTML, CSS, and JavaScript. This collection treats each layer
as observable and analyzable — HTML structure is validated, CSS properties are
checked, JavaScript errors are classified, and design systems are reverse-engineered
from live websites. Every operation produces an artifact so the analysis is auditable.

## Roles

| Role | Purpose |
|------|---------|
| `html_css_core` | Validate HTML5 semantic structure, check CSS syntax, generate responsive boilerplate, audit ARIA landmarks |
| `javascript_debug` | Check JS syntax, lint with eslint, analyze error patterns, verify source maps, detect unhandled rejections |
| `design_research` | Fetch a target URL, extract CSS tokens (colors/fonts/spacing), capture layout structure, detect CSS framework |

## Dependencies

- Python 3.9+ stdlib (`html.parser`, `re`, `argparse`, `json`, `urllib`)
- `cssutils` (optional) — for structured CSS parsing in design_research
- Node.js + eslint (optional) — for JS linting in javascript_debug
- `general_ludd.agent` >= 0.1.0 (provides `gludd_model_call`, `gludd_message`, `gludd_facts`)
