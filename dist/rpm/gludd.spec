Name: gludd
Version: VERSION_PLACEHOLDER
Release: 1
Summary: General Ludd Agent - autonomous coding system
License: MIT
URL: https://github.com/sandboxcom/gludd
Source0: gludd
BuildArch: x86_64
Vendor: sandboxcom
Packager: General Ludd <noreply@sandboxcom.github.io>

%description
Autonomous coding system with Ansible runners and multi-model AI agents.
Standalone binary - no Python runtime required.

%prep

%build

%install
mkdir -p %{buildroot}/usr/bin
cp %{SOURCE0} %{buildroot}/usr/bin/gludd
chmod 755 %{buildroot}/usr/bin/gludd

%files
%attr(755,root,root) /usr/bin/gludd

%changelog
* Thu Jul 24 2026 General Ludd <noreply@sandboxcom.github.io> - 0.1.0-beta.1-1
- Initial beta release
