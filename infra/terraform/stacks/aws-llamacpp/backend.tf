# Local backend for development/testing.
# The TerraformGenerator in Python code selects the real backend
# (S3 / GCS / Azurerm / pg / local) via StateBackendSelector at apply time.
terraform {
  backend "local" {
    path = "terraform.tfstate"
  }
}
