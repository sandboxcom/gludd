# General Ludd — distribution tarball

This archive contains a self-contained General Ludd build plus the files needed
to install and run it as a service.

## Contents

- `gludd` — the General Ludd executable.
- `install.sh` — installs the binary to `/usr/local/bin`, creates the config and
  data directories, and installs the systemd unit. It does **not** auto-start the
  service — review your config first.
- `general-ludd.service` — systemd unit (runs as a dedicated user, binds to
  `127.0.0.1`, reads its environment from an `EnvironmentFile`).
- `config/`, `templates/`, `playbooks/` — default configuration and assets.

## Quick start

```sh
./install.sh                 # installs binary + service (does not start it)
sudoedit /etc/general-ludd/general-ludd.yml   # review the main config
sudo systemctl start general-ludd            # start when ready
```

The main user-facing configuration file is `general-ludd.yml`. See the project
documentation (`docs/quickstart.md`, `docs/configuration.md`) for details.
