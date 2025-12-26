%global pypi_name acm-switchover
%global pkg_name acm-switchover

Name:           %{pkg_name}
Version:        1.5.0
Release:        1%{?dist}
Summary:        Automated Red Hat ACM Hub Switchover Tool

License:        MIT
URL:            https://github.com/tomazb/rh-acm-switchover
Source0:        %{url}/archive/v%{version}/%{name}-%{version}.tar.gz

BuildArch:      noarch

BuildRequires:  python3-devel
BuildRequires:  python3-setuptools
BuildRequires:  pandoc
BuildRequires:  gzip

Requires:       python3 >= 3.9
Requires:       python3-kubernetes >= 28.0.0
Requires:       python3-pyyaml >= 6.0
Requires:       python3-rich >= 13.0.0
Requires:       python3-tenacity >= 8.2.0
Requires:       python3-urllib3 >= 2.5.0

%description
Automated, idempotent tool for switching over Red Hat Advanced Cluster
Management (ACM) from a primary hub to a secondary hub cluster. Features
comprehensive validation, state tracking, and rollback capabilities.

%prep
%autosetup -n rh-acm-switchover-%{version}

%build
# Build man pages
make -C packaging/common/man

%install
# Create directories
install -d %{buildroot}%{_bindir}
install -d %{buildroot}%{_libexecdir}/%{pkg_name}
install -d %{buildroot}%{_datadir}/%{pkg_name}
install -d %{buildroot}%{_datadir}/%{pkg_name}/lib
install -d %{buildroot}%{_datadir}/%{pkg_name}/modules
install -d %{buildroot}%{_datadir}/%{pkg_name}/deploy
install -d %{buildroot}%{_datadir}/bash-completion/completions
install -d %{buildroot}%{_mandir}/man1
install -d %{buildroot}%{_sysconfdir}/sysconfig
install -d %{buildroot}%{_sharedstatedir}/%{pkg_name}

# Install Python modules
install -p -m 644 acm_switchover.py %{buildroot}%{_datadir}/%{pkg_name}/
install -p -m 644 check_rbac.py %{buildroot}%{_datadir}/%{pkg_name}/
install -p -m 644 show_state.py %{buildroot}%{_datadir}/%{pkg_name}/
cp -rp lib/* %{buildroot}%{_datadir}/%{pkg_name}/lib/
cp -rp modules/* %{buildroot}%{_datadir}/%{pkg_name}/modules/

# Install helper scripts
install -p -m 755 quick-start.sh %{buildroot}%{_libexecdir}/%{pkg_name}/
install -p -m 755 scripts/*.sh %{buildroot}%{_libexecdir}/%{pkg_name}/

# Install deploy manifests
cp -rp deploy/* %{buildroot}%{_datadir}/%{pkg_name}/deploy/

# Install wrapper scripts
cat > %{buildroot}%{_bindir}/acm-switchover << 'EOF'
#!/bin/sh
# ACM Switchover wrapper script
[ -r /etc/sysconfig/acm-switchover ] && . /etc/sysconfig/acm-switchover
: "${ACM_SWITCHOVER_STATE_DIR:=/var/lib/acm-switchover}"
export ACM_SWITCHOVER_STATE_DIR
export PYTHONPATH="%{_datadir}/%{pkg_name}:${PYTHONPATH}"
exec /usr/bin/python3 -c "from acm_switchover import main; main()" "$@"
EOF
chmod 755 %{buildroot}%{_bindir}/acm-switchover

cat > %{buildroot}%{_bindir}/acm-switchover-rbac << 'EOF'
#!/bin/sh
# ACM Switchover RBAC checker wrapper script
[ -r /etc/sysconfig/acm-switchover ] && . /etc/sysconfig/acm-switchover
export PYTHONPATH="%{_datadir}/%{pkg_name}:${PYTHONPATH}"
exec /usr/bin/python3 -c "from check_rbac import main; main()" "$@"
EOF
chmod 755 %{buildroot}%{_bindir}/acm-switchover-rbac

cat > %{buildroot}%{_bindir}/acm-switchover-state << 'EOF'
#!/bin/sh
# ACM Switchover state viewer wrapper script
[ -r /etc/sysconfig/acm-switchover ] && . /etc/sysconfig/acm-switchover
: "${ACM_SWITCHOVER_STATE_DIR:=/var/lib/acm-switchover}"
export ACM_SWITCHOVER_STATE_DIR
export PYTHONPATH="%{_datadir}/%{pkg_name}:${PYTHONPATH}"
exec /usr/bin/python3 -c "from show_state import main; main()" "$@"
EOF
chmod 755 %{buildroot}%{_bindir}/acm-switchover-state

# Install bash completions
install -p -m 644 completions/acm_switchover.py %{buildroot}%{_datadir}/bash-completion/completions/acm-switchover
install -p -m 644 completions/check_rbac.py %{buildroot}%{_datadir}/bash-completion/completions/acm-switchover-rbac
install -p -m 644 completions/show_state.py %{buildroot}%{_datadir}/bash-completion/completions/acm-switchover-state

# Install man pages
install -p -m 644 packaging/common/man/*.1.gz %{buildroot}%{_mandir}/man1/

# Install sysconfig file
cat > %{buildroot}%{_sysconfdir}/sysconfig/acm-switchover << 'EOF'
# ACM Switchover configuration
# This file is sourced by the wrapper scripts

# State directory (default: /var/lib/acm-switchover)
# ACM_SWITCHOVER_STATE_DIR=/var/lib/acm-switchover
EOF

%post
# Ensure state directory has correct permissions
if [ ! -d %{_sharedstatedir}/%{pkg_name} ]; then
    mkdir -p %{_sharedstatedir}/%{pkg_name}
fi
chmod 0750 %{_sharedstatedir}/%{pkg_name}

%files
%license LICENSE
%doc README.md CHANGELOG.md docs/
%{_bindir}/acm-switchover
%{_bindir}/acm-switchover-rbac
%{_bindir}/acm-switchover-state
%{_libexecdir}/%{pkg_name}/
%{_datadir}/%{pkg_name}/
%{_datadir}/bash-completion/completions/acm-switchover
%{_datadir}/bash-completion/completions/acm-switchover-rbac
%{_datadir}/bash-completion/completions/acm-switchover-state
%{_mandir}/man1/acm-switchover.1*
%{_mandir}/man1/acm-switchover-rbac.1*
%{_mandir}/man1/acm-switchover-state.1*
%config(noreplace) %{_sysconfdir}/sysconfig/acm-switchover
%dir %attr(0750,root,root) %{_sharedstatedir}/%{pkg_name}

%changelog
* Mon Dec 22 2025 Tomaz Borstnar <tomaz@borstnar.com> - 1.5.0-1
- Initial packaging release
- Full packaging support with version sync tooling
- Man pages and bash completions
- State directory defaults to /var/lib/acm-switchover
