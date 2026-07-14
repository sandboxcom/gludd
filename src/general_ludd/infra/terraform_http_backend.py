"""Generate terraform ``backend "http"`` blocks pointing at the gludd API.

The generated block routes state-locking and state-read/write operations
through the gludd daemon's ``/api/terraform/state/<stack>`` endpoint so
concurrent terraform runs share a single externally-managed state.
"""

from __future__ import annotations

import os
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    pass


def generate_http_backend_block(
    stack_name: str,
    api_url: str,
    psk: str | None = None,
) -> str:
    """Return the HCL terraform ``backend "http"`` block as a string.

    The block configures ``address``, ``lock_address``, and
    ``unlock_address`` to point at the gludd state API endpoint for
    *stack_name*.
    """
    endpoint = f"{api_url}/api/terraform/state/{stack_name}"
    return (
        'terraform {\n'
        '  backend "http" {\n'
        f'    address = "{endpoint}"\n'
        f'    lock_address = "{endpoint}"\n'
        f'    unlock_address = "{endpoint}"\n'
        '  }\n'
        '}\n'
    )


def write_http_backend_file(
    stack_name: str,
    stack_dir: str,
    api_url: str,
    psk: str | None = None,
) -> str:
    """Write the backend block to ``{stack_dir}/backend.tf``.

    Returns the absolute path of the written file.
    """
    content = generate_http_backend_block(stack_name, api_url, psk)
    filepath = os.path.join(stack_dir, "backend.tf")
    with open(filepath, "w") as fh:
        fh.write(content)
    return os.path.abspath(filepath)
