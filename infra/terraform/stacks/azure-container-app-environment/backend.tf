# HTTP backend — state stored via the Gludd daemon API when this canonical
# stack is used directly. The owned live runtime materializes a state-isolated
# copy and initializes it with -backend=false.
terraform {
  backend "http" {
    address        = "http://localhost:8400/api/terraform/state/azure-container-app-environment"
    lock_address   = "http://localhost:8400/api/terraform/state/azure-container-app-environment"
    unlock_address = "http://localhost:8400/api/terraform/state/azure-container-app-environment"
  }
}
