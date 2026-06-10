"""Terraform config generation for ephemeral GPU compute."""

from __future__ import annotations

import textwrap

from general_ludd.infra.compute import ComputeConfig, ComputeProvider, InferenceEngine

_AWS_GPU_TO_INSTANCE: dict[str, str] = {
    "t4": "g4dn.xlarge",
    "a10g": "g5.xlarge",
    "a100_80": "p4d.24xlarge",
    "a100_40": "p4d.24xlarge",
    "h100": "p5.48xlarge",
}

_AWS_GPU_TO_AMI_FILTER: dict[str, str] = {
    "t4": "Deep Learning AMI GPU CUDA_*",
    "a10g": "Deep Learning AMI GPU CUDA_*",
    "a100_80": "Deep Learning AMI GPU CUDA_*",
    "h100": "Deep Learning AMI GPU CUDA_*",
}

_GCP_GPU_TO_TYPE: dict[str, str] = {
    "l4": "nvidia-l4",
    "t4": "nvidia-tesla-t4",
    "a100_80": "nvidia-tesla-a100",
    "h100": "nvidia-h100-80gb",
}

_GCP_MACHINE_TYPES: dict[str, str] = {
    "l4": "g2-standard-4",
    "t4": "n1-standard-4",
    "a100_80": "a2-highgpu-1g",
    "h100": "a3-highgpu-1g",
}


def _default_image(engine: InferenceEngine) -> str:
    if engine == InferenceEngine.LLAMACPP:
        return "ghcr.io/ggerganov/llama.cpp:server"
    return "vllm/vllm-openai:latest"


def _container_image(config: ComputeConfig) -> str:
    if config.container_image:
        return config.container_image
    return _default_image(config.engine)


def _engine_serve_cmd(config: ComputeConfig) -> str:
    image = _container_image(config)
    if config.engine == InferenceEngine.LLAMACPP:
        return (
            f"docker run --gpus all -p 8000:8000 {image} "
            f"-m {config.model_name} --host 0.0.0.0 --port 8000"
        )
    return (
        f"docker run --gpus all -p 8000:8000 {image} "
        f"--model {config.model_name} --host 0.0.0.0 --port 8000"
    )


def _user_data_script(config: ComputeConfig) -> str:
    serve_cmd = _engine_serve_cmd(config)
    script = (
        "#!/bin/bash\n"
        "set -euxo pipefail\n"
        "\n"
        "# Pull and run inference server\n"
        f"{serve_cmd} &\n"
        "\n"
        "# Cost/TTL watchdog\n"
        f'echo "MAX_COST={config.max_cost_usd}" >> /etc/environment\n'
        f'echo "TIMEOUT_MIN={config.timeout_minutes}" >> /etc/environment\n'
    )
    return script


class TerraformGenerator:
    def generate(self, config: ComputeConfig) -> str:
        if config.provider == ComputeProvider.AZURE and config.deploy_type == "containerapp":
            return self._generate_azure_containerapp(config)
        dispatch = {
            ComputeProvider.AWS: self._generate_aws,
            ComputeProvider.GCP: self._generate_gcp,
            ComputeProvider.AZURE: self._generate_azure,
            ComputeProvider.RUNPOD: self._generate_runpod,
            ComputeProvider.VAST_AI: self._generate_vast_ai,
            ComputeProvider.LAMBDA_LABS: self._generate_generic,
            ComputeProvider.MODAL: self._generate_generic,
            ComputeProvider.COREWEAVE: self._generate_generic,
            ComputeProvider.DIGITAL_OCEAN: self._generate_generic,
            ComputeProvider.ORACLE: self._generate_generic,
        }
        handler = dispatch.get(config.provider, self._generate_generic)
        return handler(config)

    def _generate_aws(self, config: ComputeConfig) -> str:
        instance_type = _AWS_GPU_TO_INSTANCE.get(config.gpu_type.value, "g5.xlarge")
        region = config.region or "us-east-1"
        image = _container_image(config)
        user_data = _user_data_script(config)

        spot_block = ""
        if config.spot:
            spot_block = textwrap.dedent("""\
              instance_market_options {
                market_type = "spot"
              }
            """)

        return textwrap.dedent(f"""\
            terraform {{
              required_providers {{
                aws = {{
                      source  = "hashicorp/aws"
                      version = "~> 5.0"
                }}
              }}
            }}

            provider "aws" {{
              region = "{region}"
            }}

            data "aws_ami" "dl_ami" {{
              most_recent = true
              owners      = ["amazon"]

              filter {{
                name   = "name"
                values = ["Deep Learning AMI GPU CUDA*"]
              }}

              filter {{
                name   = "virtualization-type"
                values = ["hvm"]
              }}
            }}

            resource "aws_instance" "gpu_instance" {{
              ami           = data.aws_ami.dl_ami.id
              instance_type = "{instance_type}"
              {spot_block}
              root_block_device {{
                volume_size = {config.disk_size_gb}
                volume_type = "gp3"
              }}

              user_data = <<-EOT
            {user_data}
              EOT

              tags = {{
                Name        = "ephemeral-gpu-{config.gpu_type.value}"
                Engine      = "{config.engine.value}"
                Model       = "{config.model_name}"
                MaxCostUSD  = "{config.max_cost_usd}"
                TimeoutMin  = "{config.timeout_minutes}"
                GPUType     = "{config.gpu_type.value}"
                GPUCount    = "{config.gpu_count}"
                ContainerImage = "{image}"
              }}
            }}

            resource "aws_security_group" "gpu_sg" {{
              name_prefix = "gpu-compute-"

              ingress {{
                from_port   = 8000
                to_port     = 8000
                protocol    = "tcp"
                cidr_blocks = ["0.0.0.0/0"]
              }}

              egress {{
                from_port   = 0
                to_port     = 0
                protocol    = "-1"
                cidr_blocks = ["0.0.0.0/0"]
              }}
            }}

            output "instance_ip" {{
              value = aws_instance.gpu_instance.public_ip
            }}

            output "endpoint_url" {{
              value = "http://${{aws_instance.gpu_instance.public_ip}}:8000/v1"
            }}
        """)

    def _generate_gcp(self, config: ComputeConfig) -> str:
        gpu_type = _GCP_GPU_TO_TYPE.get(config.gpu_type.value, "nvidia-l4")
        machine_type = _GCP_MACHINE_TYPES.get(config.gpu_type.value, "g2-standard-4")
        region = config.region or "us-central1"
        zone = f"{region}-a"
        image = _container_image(config)
        user_data = _user_data_script(config)

        preemptible_block = ""
        if config.spot:
            preemptible_block = textwrap.dedent("""\
                scheduling {
                  preemptible = true
                  automatic_restart = false
                }
            """)

        return textwrap.dedent(f"""\
            terraform {{
              required_providers {{
                google = {{
                      source  = "hashicorp/google"
                      version = "~> 5.0"
                }}
              }}
            }}

            provider "google" {{
              region = "{region}"
            }}

            resource "google_compute_instance" "gpu_instance" {{
              name         = "ephemeral-gpu-{config.gpu_type.value}"
              machine_type = "{machine_type}"
              zone         = "{zone}"

              boot_disk {{
                initialize_params {{
                  image = "projects/deeplearning-platform-release/global/images/family/common-cu121"
                  size  = {config.disk_size_gb}
                }}
              }}

              network_interface {{
                network = "default"
                access_config {{}}
              }}

              guest_accelerator {{
                type  = "{gpu_type}"
                count = {config.gpu_count}
              }}

              metadata = {{
                user-data = <<-EOT
            {user_data}
                EOT
              }}

              {preemptible_block}

              labels = {{
                engine        = "{config.engine.value}"
                model         = replace("{config.model_name}", "/", "-")
                max_cost_usd  = "{config.max_cost_usd}"
                timeout_min   = "{config.timeout_minutes}"
                gpu_type      = "{config.gpu_type.value}"
                gpu_count     = "{config.gpu_count}"
                container     = "{image}"
              }}
            }}

            resource "google_compute_firewall" "gpu_fw" {{
              name    = "gpu-compute-{config.gpu_type.value}"
              network = "default"

              allow {{
                protocol = "tcp"
                ports    = ["8000"]
              }}

              source_ranges = ["0.0.0.0/0"]
              target_tags   = ["gpu-instance"]
            }}

            output "instance_ip" {{
              value = google_compute_instance.gpu_instance.network_interface[0].access_config[0].nat_ip
            }}

            output "endpoint_url" {{
              value = "http://${{google_compute_instance.gpu_instance.network_interface[0].access_config[0].nat_ip}}:8000/v1"
            }}
        """)

    def _generate_azure(self, config: ComputeConfig) -> str:
        region = config.region or "eastus"
        image = _container_image(config)
        user_data = _user_data_script(config)

        return textwrap.dedent(f"""\
            terraform {{
              required_providers {{
                azurerm = {{
                      source  = "hashicorp/azurerm"
                      version = "~> 3.0"
                }}
              }}
            }}

            provider "azurerm" {{
              features {{}}
              location = "{region}"
            }}

            resource "azurerm_resource_group" "gpu_rg" {{
              name     = "gpu-rg-{config.gpu_type.value}"
              location = "{region}"
            }}

            resource "azurerm_virtual_network" "gpu_vnet" {{
              name                = "gpu-vnet"
              address_space       = ["10.0.0.0/16"]
              location            = azurerm_resource_group.gpu_rg.location
              resource_group_name = azurerm_resource_group.gpu_rg.name
            }}

            resource "azurerm_subnet" "gpu_subnet" {{
              name                 = "gpu-subnet"
              resource_group_name  = azurerm_resource_group.gpu_rg.name
              virtual_network_name = azurerm_virtual_network.gpu_vnet.name
              address_prefixes     = ["10.0.1.0/24"]
            }}

            resource "azurerm_network_security_group" "gpu_nsg" {{
              name                = "gpu-nsg"
              location            = azurerm_resource_group.gpu_rg.location
              resource_group_name = azurerm_resource_group.gpu_rg.name

              security_rule {{
                name                       = "allow-inference"
                priority                   = 100
                direction                  = "Inbound"
                access                     = "Allow"
                protocol                   = "Tcp"
                source_port_range          = "*"
                destination_port_range     = "8000"
                source_address_prefix      = "*"
                destination_address_prefix = "*"
              }}
            }}

            resource "azurerm_virtual_machine" "gpu_vm" {{
              name                  = "gpu-vm-{config.gpu_type.value}"
              location              = azurerm_resource_group.gpu_rg.location
              resource_group_name   = azurerm_resource_group.gpu_rg.name
              network_interface_ids = [azurerm_network_interface.gpu_nic.id]
              vm_size               = "Standard_NC4as_T4_v3"

              storage_os_disk {{
                name              = "gpu-osdisk"
                caching           = "ReadWrite"
                create_option     = "FromImage"
                disk_size_gb      = {config.disk_size_gb}
              }}

              storage_image_reference {{
                publisher = "microsoft-dsvm"
                offer     = "ubuntu-hpc"
                sku       = "2204"
                version   = "latest"
              }}

              os_profile {{
                computer_name  = "gpuvm"
                admin_username = "azureuser"
                custom_data    = base64encode(<<-EOT
            {user_data}
                EOT
                )
              }}

              os_profile_linux_config {{
                disable_password_authentication = true
                ssh_keys {{
                  key_data = ""
                  path     = "/home/azureuser/.ssh/authorized_keys"
                }}
              }}

              tags = {{
                engine       = "{config.engine.value}"
                model        = "{config.model_name}"
                max_cost_usd = "{config.max_cost_usd}"
                timeout_min  = "{config.timeout_minutes}"
                gpu_type     = "{config.gpu_type.value}"
                container    = "{image}"
              }}
            }}

            resource "azurerm_network_interface" "gpu_nic" {{
              name                = "gpu-nic"
              location            = azurerm_resource_group.gpu_rg.location
              resource_group_name = azurerm_resource_group.gpu_rg.name

              ip_configuration {{
                name                          = "internal"
                subnet_id                     = azurerm_subnet.gpu_subnet.id
                private_ip_address_allocation = "Dynamic"
                public_ip_address_id          = azurerm_public_ip.gpu_pip.id
              }}
            }}

            resource "azurerm_public_ip" "gpu_pip" {{
              name                = "gpu-pip"
              location            = azurerm_resource_group.gpu_rg.location
              resource_group_name = azurerm_resource_group.gpu_rg.name
              allocation_method   = "Static"
            }}
        """)

    def _generate_azure_containerapp(self, config: ComputeConfig) -> str:
        region = config.region or "eastus"
        image = _container_image(config)
        gpu_sku_map: dict[str, str] = {
            "t4": "Standard_NC4as_T4_v3",
            "a10g": "Standard_NC24ads_A100_v4",
            "l4": "Standard_NC24ads_A100_v4",
            "a10": "Standard_NC24ads_A100_v4",
            "rtx_4090": "Standard_NC24ads_A100_v4",
            "rtx_6000_ada": "Standard_NC24ads_A100_v4",
            "a40": "Standard_ND96asr_v4",
            "l40s": "Standard_NC24ads_A100_v4",
            "a100_40": "Standard_ND96asr_v4",
            "a100_80": "Standard_ND96asr_v4",
            "h100": "Standard_ND96isr_v5",
            "h200": "Standard_ND96isr_v5",
        }
        sku = gpu_sku_map.get(config.gpu_type.value, "Standard_NC4as_T4_v3")
        acr_suffix = config.gpu_type.value.replace("_", "").replace("-", "")[:8]

        return textwrap.dedent(f"""\
            terraform {{
              required_providers {{
                azurerm = {{
                      source  = "hashicorp/azurerm"
                      version = "~> 3.0"
                }}
              }}
            }}

            provider "azurerm" {{
              features {{}}
              location = "{region}"
            }}

            resource "azurerm_resource_group" "ca_rg" {{
              name     = "ca-rg-{config.gpu_type.value}"
              location = "{region}"
            }}

            resource "azurerm_container_registry" "gpu_acr" {{
              name                = "gpuacr{acr_suffix}"
              resource_group_name = azurerm_resource_group.ca_rg.name
              location            = azurerm_resource_group.ca_rg.location
              sku                 = "Standard"
              admin_enabled       = true
            }}

            resource "azurerm_container_app_environment" "gpu_env" {{
              name                       = "gpu-inference-env"
              location                   = azurerm_resource_group.ca_rg.location
              resource_group_name        = azurerm_resource_group.ca_rg.name
              infrastructure_subnet_id   = azurerm_subnet.ca_subnet.id
            }}

            resource "azurerm_virtual_network" "ca_vnet" {{
              name                = "ca-vnet"
              address_space       = ["10.1.0.0/16"]
              location            = azurerm_resource_group.ca_rg.location
              resource_group_name = azurerm_resource_group.ca_rg.name
            }}

            resource "azurerm_subnet" "ca_subnet" {{
              name                 = "ca-subnet"
              resource_group_name  = azurerm_resource_group.ca_rg.name
              virtual_network_name = azurerm_virtual_network.ca_vnet.name
              address_prefixes     = ["10.1.0.0/23"]
            }}

            resource "azurerm_container_app" "gpu_inference" {{
              name                         = "gpu-inference-{config.gpu_type.value}"
              container_app_environment_id = azurerm_container_app_environment.gpu_env.id
              resource_group_name          = azurerm_resource_group.ca_rg.name
              revision_mode                = "Single"

              container {{
                name   = "inference"
                image  = "{image}"

                resources {{
                  cpu    = "4.0"
                  memory = "16Gi"
                }}

                env {{
                  name  = "MODEL_NAME"
                  value = "{config.model_name}"
                }}

                env {{
                  name  = "HOST"
                  value = "0.0.0.0"
                }}

                env {{
                  name  = "PORT"
                  value = "8000"
                }}

                env {{
                  name  = "NVIDIA_VISIBLE_DEVICES"
                  value = "all"
                }}
              }}

              ingress {{
                target_port = 8000
                transport   = "http"
                external_enabled = true

                traffic_weight {{
                  percentage = 100
                  latest_revision = true
                }}
              }}

              template {{
                container {{
                  name  = "inference"
                  image = "{image}"
                }}
              }}

              tags = {{
                engine       = "{config.engine.value}"
                model        = "{config.model_name}"
                gpu_type     = "{config.gpu_type.value}"
                compute_type = "containerapp"
                vm_sku       = "{sku}"
                gpu_required = "true"
                nvidia_gpu   = "{config.gpu_type.value}"
              }}
            }}

            output "endpoint_url" {{
              value = azurerm_container_app.gpu_inference.latest_revision_fqdn
            }}
        """)

    def _generate_runpod(self, config: ComputeConfig) -> str:
        image = _container_image(config)
        gpu_types: dict[str, str] = {
            "l4": "NVIDIA L4",
            "a100_80": "NVIDIA A100 80GB",
            "a100_40": "NVIDIA A100 40GB",
            "rtx_4090": "RTX 4090",
            "h100": "NVIDIA H100 80GB",
        }
        gpu_name = gpu_types.get(config.gpu_type.value, f"NVIDIA {config.gpu_type.value}")

        return textwrap.dedent(f"""\
            terraform {{
              required_providers {{
                runpod = {{
                      source  = "runpod/runpod"
                      version = "~> 1.0"
                }}
              }}
            }}

            provider "runpod" {{}}

            resource "runpod_pod" "gpu_pod" {{
              name         = "ephemeral-gpu-{config.gpu_type.value}"
              image_name   = "{image}"
              gpu_type_id  = "{gpu_name}"
              gpu_count    = {config.gpu_count}
              container_disk_size_gb = {config.disk_size_gb}

              env = [
                {{
                  key   = "MODEL_NAME"
                  value = "{config.model_name}"
                }},
                {{
                  key   = "ENGINE"
                  value = "{config.engine.value}"
                }},
                {{
                  key   = "MAX_COST_USD"
                  value = "{config.max_cost_usd}"
                }},
                {{
                  key   = "TIMEOUT_MIN"
                  value = "{config.timeout_minutes}"
                }},
              ]{"true" if False else ""}

              ports = {{
                http  = 8000
              }}

              tags = {{
                engine       = "{config.engine.value}"
                gpu_type     = "{config.gpu_type.value}"
                model        = "{config.model_name}"
                container    = "{image}"
              }}
            }}

            output "pod_id" {{
              value = runpod_pod.gpu_pod.id
            }}

            output "endpoint_url" {{
              value = "${{runpod_pod.gpu_pod.default_domain}}:8000/v1"
            }}
        """)

    def _generate_vast_ai(self, config: ComputeConfig) -> str:
        image = _container_image(config)

        return textwrap.dedent(f"""\
            terraform {{
              required_providers {{
                vast-ai = {{
                      source  = "vast-ai/vast-ai"
                      version = "~> 1.0"
                }}
              }}
            }}

            provider "vast-ai" {{}}

            resource "vast-ai_instance" "gpu_instance" {{
              image       = "{image}"
              gpu_name    = "{config.gpu_type.value}"
              gpu_count   = {config.gpu_count}
              disk_size   = {config.disk_size_gb}
              region      = "{config.region or "us"}"

              env = {{
                MODEL_NAME    = "{config.model_name}"
                ENGINE        = "{config.engine.value}"
                MAX_COST_USD  = "{config.max_cost_usd}"
                TIMEOUT_MIN   = "{config.timeout_minutes}"
              }}

              ports = ["8000:8000"]

              tags = {{
                engine   = "{config.engine.value}"
                gpu_type = "{config.gpu_type.value}"
              }}
            }}

            output "instance_ip" {{
              value = vast-ai_instance.gpu_instance.public_ip
            }}
        """)

    def _generate_generic(self, config: ComputeConfig) -> str:
        image = _container_image(config)
        provider_name = config.provider.value

        return textwrap.dedent(f"""\
            terraform {{
              required_providers {{
                {provider_name} = {{
                      source  = "{provider_name}/{provider_name}"
                      version = ">= 1.0"
                }}
              }}
            }}

            provider "{provider_name}" {{}}

            resource "{provider_name}_instance" "gpu_instance" {{
              name        = "ephemeral-gpu-{config.gpu_type.value}"
              gpu_type    = "{config.gpu_type.value}"
              gpu_count   = {config.gpu_count}
              image       = "{image}"
              disk_size   = {config.disk_size_gb}

              env = {{
                MODEL_NAME    = "{config.model_name}"
                ENGINE        = "{config.engine.value}"
                MAX_COST_USD  = "{config.max_cost_usd}"
                TIMEOUT_MIN   = "{config.timeout_minutes}"
              }}

              ports = ["8000:8000"]

              tags = {{
                engine   = "{config.engine.value}"
                gpu_type = "{config.gpu_type.value}"
                model    = "{config.model_name}"
              }}
            }}

            output "instance_endpoint" {{
              value = "${{{provider_name}_instance.gpu_instance.endpoint}}:8000/v1"
            }}
        """)
