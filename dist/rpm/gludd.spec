Name:           gludd
Version:        %(echo "VERSION_PLACEHOLDER" | tr '-' '~')
Release:        1
Summary:        General Ludd Agent - autonomous coding system
License:        MIT
URL:            https://github.com/sandboxcom/gludd
Source0:        gludd
BuildArch:      x86_64
Vendor:         sandboxcom
Packager:       General Ludd <noreply@sandboxcom.github.io>

%description
Autonomous coding system with Ansible runners and multi-model AI agents.
Standalone binary - no Python runtime required.

%prep
:

%build
:

%install
install -d %{buildroot}%{_bindir}
install -m 0755 %{SOURCE0} %{buildroot}%{_bindir}/gludd

%files
%attr(0755, root, root) %{_bindir}/gludd

%changelog
* Thu Jul 24 2026 General Ludd <noreply@sandboxcom.github.io> - 0.1.0-1
- Initial beta release
