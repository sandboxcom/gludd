Name: gludd
Version: VERSION_PLACEHOLDER
Release: 1
Summary: General Ludd Agent - autonomous coding system
License: MIT
URL: https://github.com/sandboxcom/gludd
Source0: gludd
Requires: python3 >= 3.11

%description
Autonomous coding system with Ansible runners and multi-model AI agents.

%prep

%build

%install
mkdir -p $RPM_BUILD_ROOT/usr/bin
cp %{SOURCE0} $RPM_BUILD_ROOT/usr/bin/gludd
chmod 755 $RPM_BUILD_ROOT/usr/bin/gludd

%files
/usr/bin/gludd
