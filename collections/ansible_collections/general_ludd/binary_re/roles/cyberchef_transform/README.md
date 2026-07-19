# cyberchef_transform

CyberChef API transformation role for the `general_ludd.binary_re` collection.

## Description

Executes CyberChef recipes via the CyberChef API: encoding, decoding,
encryption, compression, and custom recipe pipelines. Report-only.

## Variables

| Variable | Default | Description |
|---|---|---|
| `cyberchef_url` | `http://localhost:3000` | CyberChef API base URL |
| `output_dir` | `/tmp/gludd-cyberchef-transform` | Artifact output directory |
| `enable_encoding` | `false` | Run encoding pipeline |
| `enable_decoding` | `false` | Run decoding pipeline |
| `enable_encryption` | `false` | Run encryption pipeline |
| `enable_compression` | `false` | Run compression pipeline |
| `recipe_file` | `""` | Path to custom recipe file |

## Artifacts

- `<output_dir>/cyberchef_transform.json` — transform summary artifact
