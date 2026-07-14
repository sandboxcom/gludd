locals {
  cloud_init_full = <<-EOT
    #cloud-config
    package_update: true
    packages:
      - docker.io
      - nvidia-container-toolkit
      - qemu-guest-agent

    users:
      - name: gludd
        ssh_authorized_keys:
          - ${var.ssh_public_key}
        sudo: ALL=(ALL) NOPASSWD:ALL
        shell: /bin/bash
        lock_passwd: true

    runcmd:
      - systemctl enable --now docker
      - systemctl enable --now qemu-guest-agent
      - |
        cat > /usr/local/bin/launch-inference.sh << 'BOOT'
        ${var.cloud_init_user_data}
        BOOT
        chmod +x /usr/local/bin/launch-inference.sh
        /usr/local/bin/launch-inference.sh
  EOT
}

resource "terraform_data" "qemu_vm_config" {
  input = {
    name                = var.name
    vcpus               = var.vcpus
    memory_mb           = var.memory_mb
    disk_size_gb        = var.disk_size_gb
    base_image_url      = var.base_image_url
    storage_pool        = var.storage_pool
    network_name        = var.network_name
    host_port           = var.host_port
    ssh_public_key      = var.ssh_public_key
    cloud_init_user_data = var.cloud_init_user_data
    cloud_init_full     = local.cloud_init_full
  }
}
