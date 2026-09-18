# Installation and Deployment Guide

## System Requirements

### Prerequisites

- **Python**: 3.10 or later
- **Kubernetes CLI**: `kubectl` or `oc` (OpenShift CLI)
- **Access**: Kubernetes contexts configured for both hubs
- **Permissions**: RBAC access to ACM resources on both clusters

### Verify Requirements

```bash
# Check Python version
python3 --version
# Should show: Python 3.10.x or later

# Check kubectl/oc
kubectl version --client
# OR
oc version --client

# List available contexts
kubectl config get-contexts
# OR
oc config get-contexts
```

### Upgrading Python (if needed)

If your system has Python 3.9 or earlier, you must upgrade to Python 3.10+:

**RHEL 8.8+ / RHEL 9 (AppStream):**

```bash
sudo dnf install python3.11 python3.11-pip
python3.11 -m venv venv
source venv/bin/activate
```

**Ubuntu 22.04+:**

```bash
sudo apt install python3.10 python3.10-venv
python3.10 -m venv venv
source venv/bin/activate
```

**Using pyenv:**

```bash
pyenv install 3.11.0
pyenv local 3.11.0
python -m venv venv
source venv/bin/activate
```

Alternatively, use the [container image](container.md) which includes Python 3.12 and all dependencies.

## Installation Methods

### Method 1: Clone from Git (Recommended)

```bash
# Clone repository
git clone https://github.com/tomazb/rh-acm-switchover.git
cd rh-acm-switchover

# Create virtual environment
python3 -m venv venv
source venv/bin/activate

# Install dependencies
pip install --upgrade pip
pip install -r requirements.txt

# Verify installation
python acm_switchover.py --help
```

### Method 2: Download Release Archive

```bash
# Download latest release
wget https://github.com/tomazb/rh-acm-switchover/archive/refs/heads/main.zip
unzip main.zip
cd rh-acm-switchover-main

# Install dependencies
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

### Method 3: System-Wide Installation

```bash
# Clone repository
git clone https://github.com/tomazb/rh-acm-switchover.git
cd rh-acm-switchover

# Install dependencies system-wide (requires sudo)
sudo pip3 install -r requirements.txt

# Add to PATH (optional)
sudo cp acm_switchover.py /usr/local/bin/acm-switchover
sudo chmod +x /usr/local/bin/acm-switchover

# Run from anywhere
acm-switchover --help
```

## Dependency Installation

### Requirements

The tool requires these Python packages:

```
kubernetes>=28.0.0  # Kubernetes API client
PyYAML>=6.0         # YAML parsing
rich>=13.0.0        # Rich text formatting (optional)
tenacity>=8.2.0     # Retry logic
```

### Offline Installation

For environments without internet access:

```bash
# On a machine with internet:
pip download -r requirements.txt -d ./packages

# Transfer ./packages directory to offline machine

# On offline machine:
pip install --no-index --find-links=./packages -r requirements.txt
```

## Kubernetes Access Setup

### Configure Contexts

The script requires Kubernetes contexts for both hubs.

**List existing contexts:**
```bash
kubectl config get-contexts
```

**Add new context:**
```bash
# For kubectl
kubectl config set-cluster primary-hub --server=https://api.primary.example.com:6443
kubectl config set-credentials admin-user --token=<token>
kubectl config set-context primary-hub --cluster=primary-hub --user=admin-user

# For oc (OpenShift)
oc login https://api.primary.example.com:6443 --token=<token>
# Context is created automatically
```

**Rename context:**
```bash
kubectl config rename-context old-name new-name
```

### Verify Access

```bash
# Test primary hub access
kubectl --context primary-hub get namespaces

# Test secondary hub access
kubectl --context secondary-hub get namespaces

# Check ACM namespace
kubectl --context primary-hub get ns open-cluster-management
```

## RBAC Permissions

### Required Permissions

The permissions are defined once in the shipped manifests, not restated here:

- `deploy/rbac/` — the baseline operator and validator permissions both hubs need
- `deploy/rbac/extensions/decommission/` — the additional old-hub teardown permissions

[RBAC requirements](../deployment/rbac-requirements.md) documents the full permission matrix and
[RBAC deployment](../deployment/rbac-deployment.md) covers every supported way to deploy it
(kubectl, Kustomize, Helm, ACM policy, or the collection `playbooks/rbac_bootstrap.yml`).

The minimal direct apply:

```bash
# Apply baseline RBAC resources
kubectl apply -f deploy/rbac/namespace.yaml
kubectl apply -f deploy/rbac/serviceaccount.yaml
kubectl apply -f deploy/rbac/clusterrole.yaml
kubectl apply -f deploy/rbac/clusterrolebinding.yaml
kubectl apply -f deploy/rbac/role.yaml
kubectl apply -f deploy/rbac/rolebinding.yaml

# Old-hub teardown only: add the decommission extension
kubectl apply -f deploy/rbac/extensions/decommission/clusterrole.yaml
kubectl apply -f deploy/rbac/extensions/decommission/clusterrolebinding.yaml

# Verify deployment
kubectl get sa,clusterrole,role -n acm-switchover
```

Decommission additionally reads ClusterServiceVersions, Deployments and ReplicaSets in the
`open-cluster-management` namespace to identify the ACM operator and classify Pod ownership during
teardown. The shipped manifests above already include those reads; RBAC applied from an earlier
version does not, so re-apply it before running decommission.

### Create Service Account (Optional)

For automated execution:

```bash
# Create namespace (if not already present)
kubectl apply -f deploy/rbac/namespace.yaml

# Create the service account and bind the shipped ClusterRole
# (deploy/rbac/serviceaccount.yaml creates acm-switchover-operator in acm-switchover;
#  deploy/rbac/clusterrolebinding.yaml binds it to the acm-switchover-operator ClusterRole)
kubectl apply -f deploy/rbac/serviceaccount.yaml
kubectl apply -f deploy/rbac/clusterrolebinding.yaml

# Get token
kubectl create token acm-switchover-operator -n acm-switchover --duration=24h

# Configure context with service account
kubectl config set-credentials acm-switchover --token=<token>
kubectl config set-context acm-switchover-context \
  --cluster=<cluster> \
  --user=acm-switchover
```

## Post-Installation Verification

### Quick Test

```bash
# Activate the virtual environment you created earlier (if using one)
# If you created `.venv`:
source .venv/bin/activate
# OR, if you created `venv`:
source venv/bin/activate

# Run help command
python acm_switchover.py --help

# Expected output: Help text with all options
```

### Enable Bash Completions (oc/kubectl)

We ship bash completions for the main supported entry points and operator scripts, including the Python CLIs, discovery/pre-postflight helpers, and kubeconfig generators.

```bash
# Install completions (auto-detects system vs user install)
./scripts/install-completions.sh

# Or explicitly choose install location:
./scripts/install-completions.sh --user    # ~/.local/share/bash-completion/completions
sudo ./scripts/install-completions.sh --system  # /usr/share/bash-completion/completions

# Verify installation
./scripts/install-completions.sh test-completion
```

Notes:
- Supports both `oc` and `kubectl` automatically.
- Context suggestions are cached for 60s and refreshed automatically.
- SELinux: `restorecon` runs automatically when available; directory defaults pick the context type.
- After install, open a new shell or source your bash completion file.

### Validation Test

```bash
# Run validation against your hubs
python acm_switchover.py \
  --validate-only \
  --primary-context your-primary-hub \
  --secondary-context your-secondary-hub

# Expected output: Validation results with ✓ checks
```

### Syntax Check

```bash
# Verify all Python files compile
python3 -m py_compile acm_switchover.py lib/*.py modules/*.py

# No output = success
```

## Directory Structure After Installation

```
rh-acm-switchover/
├── acm_switchover.py          # Main script
├── quick-start.sh             # Interactive wizard
├── requirements.txt           # Dependencies
│
├── container-bootstrap/       # Container build resources
│   ├── Containerfile
│   └── get-pip.py
│
├── lib/                       # Core libraries
│   ├── __init__.py
│   ├── kube_client.py
│   └── utils.py
│
├── modules/                   # Switchover modules
│   ├── __init__.py
│   ├── preflight.py
│   ├── primary_prep.py
│   ├── activation.py
│   ├── post_activation.py
│   ├── finalization.py
│   └── decommission.py
│
├── scripts/                   # Helper scripts
│   ├── constants.sh
│   ├── preflight-check.sh
│   └── postflight-check.sh
│
├── docs/                      # Documentation
│   ├── ACM_SWITCHOVER_RUNBOOK.md
│   ├── getting-started/container.md
│   └── ...
│
├── .state/                    # State files (created at runtime)
│   └── switchover-<primary>__<secondary>.json
│
└── venv/                      # Virtual environment (if using venv)
```

## Environment Variables

### Optional Configuration

```bash
# Set default Kubernetes config location
export KUBECONFIG=/path/to/kubeconfig

# Set default state directory
export ACM_SWITCHOVER_STATE_DIR=/path/to/state

# Precedence: --state-file > ACM_SWITCHOVER_STATE_DIR > .state/
# Unsafe values (e.g. path traversal like "../dir") are rejected with an error
# by both the CLI and show_state.py; unset the variable or pass an explicit path.

# Enable debug logging
export ACM_SWITCHOVER_DEBUG=1
```

## Container Deployment

For detailed instructions on building and running the tool as a container, please refer to [container.md](container.md).

The project includes a production-ready `Containerfile` in `container-bootstrap/` and supports:
- Multi-stage builds (UBI 9 minimal base)
- Multi-architecture support (amd64, arm64)
- Pre-installed prerequisites (oc, kubectl, jq)
- Non-root execution

### Quick Container Run

```bash
# Using the published image
podman run -it --rm \
  -v ~/.kube:/root/.kube:ro \
  -v ./state:/var/lib/acm-switchover \
  quay.io/tomazborstnar/acm-switchover:latest --help
```

## Kubernetes Job Deployment

Create `job.yaml`:

```yaml
apiVersion: batch/v1
kind: Job
metadata:
  name: acm-switchover
  namespace: default
spec:
  template:
    spec:
      serviceAccountName: acm-switchover
      containers:
      - name: switchover
        image: acm-switchover:latest
        args:
        - --primary-context
        - primary-hub
        - --secondary-context
        - secondary-hub
        - --method
        - passive
        - --verbose
        volumeMounts:
        - name: state
          mountPath: /.state
      volumes:
      - name: state
        persistentVolumeClaim:
          claimName: acm-switchover-state
      restartPolicy: OnFailure
```

## Troubleshooting Installation

### Python Version Issues

```bash
# Check Python version
python3 --version

# If too old, install newer Python
# (method varies by OS)

# Use specific Python version
python3.11 -m venv venv
```

### Dependency Installation Failures

```bash
# Upgrade pip first
pip install --upgrade pip

# Install dependencies one by one
pip install kubernetes
pip install PyYAML
pip install rich
```

### Kubernetes Access Issues

```bash
# Verify kubeconfig
kubectl config view

# Test context access
kubectl --context your-context get nodes

# Check permissions
kubectl --context your-context auth can-i get managedclusters
```

### Permission Denied

```bash
# Make scripts executable
chmod +x acm_switchover.py
chmod +x quick-start.sh

# Or run with python explicitly
python acm_switchover.py --help
```

## Upgrade Guide

### Upgrading from Previous Version

```bash
# Pull latest changes
cd rh-acm-switchover
git pull origin main

# Update dependencies
source venv/bin/activate
pip install --upgrade -r requirements.txt

# Verify upgrade
python acm_switchover.py --help
```

### Migrating State Files

State files are backward compatible. No migration needed.

If state format changes in future:

```bash
# Backup current state
cp .state/switchover-<primary>__<secondary>.json .state/switchover-<primary>__<secondary>.json.backup

# Run migration script (if provided in future)
python migrate_state.py --input .state/switchover-<primary>__<secondary>.json
```

## Uninstallation

### Remove Virtual Environment Installation

```bash
cd rh-acm-switchover
deactivate  # Exit venv
cd ..
rm -rf rh-acm-switchover
```

### Remove System-Wide Installation

```bash
# Remove from PATH
sudo rm /usr/local/bin/acm-switchover

# Uninstall dependencies (careful - may affect other tools)
sudo pip3 uninstall kubernetes PyYAML rich
```

## Next Steps

After installation:

1. **Review documentation:**
   - [Quick Reference](../operations/quickref.md) - Command cheat sheet
   - [Usage Guide](../operations/usage.md) - Detailed examples
   - [Architecture](../development/architecture.md) - Design details

2. **Run validation:**
   ```bash
   python acm_switchover.py --validate-only \
     --primary-context <your-primary> \
     --secondary-context <your-secondary>
   ```

3. **Practice in test environment:**
   - Test full switchover workflow
   - Practice rollback procedure
   - Verify decommission process

4. **Plan production switchover:**
   - Schedule maintenance window
   - Notify stakeholders
   - Prepare rollback plan

## Support

For issues during installation:

- Check [Troubleshooting](#troubleshooting-installation) section above
- Review error messages carefully
- Verify all prerequisites are met
- Open an issue on GitHub with:
  - Python version
  - Operating system
  - Error messages
  - Steps to reproduce
