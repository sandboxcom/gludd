# HTTP backend — state stored via gludd daemon API.
# The StateBackendSelector in Python code generates the exact backend config at deploy time.
# Default: localhost:8400 for local daemon; override via GLUDD_API_URL env var or terraform init -backend-config.
terraform {
  backend "http" {
    address = "http://localhost:8400/api/terraform/state/aws-vllm"
    lock_address = "http://localhost:8400/api/terraform/state/aws-vllm"
    unlock_address = "http://localhost:8400/api/terraform/state/aws-vllm"
  }
}
