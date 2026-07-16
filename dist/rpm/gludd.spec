Name:           gludd
Version:        VERSION_PLACEHOLDER
Release:        1%{?dist}
Summary:        General Ludd Agent — autonomous coding system with ansible runners
License:        MIT
URL:            https://github.com/sandboxcom/gludd
BuildArch:      x86_64

%description
General Ludd Agent is a multi-model autonomous coding agent with ansible
runners, daemon, CLI, TUI, event loop, and agent management.  Includes
support for CI pipelines, security scanning, test execution, and
infrastructure as code.

%install
mkdir -p %{buildroot}/usr/bin
cp %{_sourcedir}/gludd %{buildroot}/usr/bin/gludd
chmod 755 %{buildroot}/usr/bin/gludd

%files
/usr/bin/gludd

%changelog
* Tue Jul 15 2026 General Ludd <gludd@sandboxcom.example> - VERSION_PLACEHOLDER-1
- Beta release
