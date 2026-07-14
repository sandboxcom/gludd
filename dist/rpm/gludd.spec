Name:           gludd
Version:        VERSION_PLACEHOLDER
Release:        1%{?dist}
Summary:        General Ludd Agent — autonomous coding system
License:        MIT
URL:            https://github.com/sandboxcom/gludd

BuildArch:      x86_64

Requires:       python3
Requires:       git
Requires:       openssh-clients
Requires:       curl
Requires:       ca-certificates

%description
gludd is an autonomous coding system with Ansible runners
and multi-model AI agents, providing a complete agentic
coding workflow with daemon management, CI integration,
and deployment orchestration.

%install
mkdir -p %{buildroot}/usr/bin
install -m 755 %{_sourcedir}/gludd %{buildroot}/usr/bin/gludd

%files
/usr/bin/gludd

%changelog
* Mon Jul 14 2026 General Ludd Team - VERSION_PLACEHOLDER-1
- Initial RPM package
