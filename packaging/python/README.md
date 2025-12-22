# Python Packaging Notes

This document explains the Python packaging strategy for ACM Switchover.

## Flat Layout

ACM Switchover uses a **flat layout** (not the typical `src/` layout) for historical and practical reasons:

```
rh-acm-switchover/
├── acm_switchover.py    # Main CLI module
├── check_rbac.py        # RBAC checker CLI module
├── show_state.py        # State viewer CLI module
├── lib/                 # Library package
│   ├── __init__.py
│   ├── kube_client.py
│   └── ...
└── modules/             # Workflow modules package
    ├── __init__.py
    ├── preflight.py
    └── ...
```

### Trade-offs

**Advantages:**
- Direct execution: `./acm_switchover.py --help` works without installation
- Simpler for users who clone the repo
- Scripts in `scripts/` can import from `lib/` directly

**Disadvantages:**
- Package names `lib` and `modules` are generic (not namespaced)
- Potential conflicts with other packages using the same names
- Not ideal for public PyPI publishing

### Future Consideration

If public PyPI publishing becomes important, consider refactoring to a namespaced package:

```
rh-acm-switchover/
├── src/
│   └── acm_switchover/
│       ├── __init__.py
│       ├── cli.py
│       ├── lib/
│       └── modules/
```

This would change imports from `from lib import KubeClient` to `from acm_switchover.lib import KubeClient`.

## Building

```bash
# Build wheel and sdist
python -m build

# Install locally for development
pip install -e .

# Install with dev dependencies
pip install -e ".[dev]"
```

## Console Scripts

After installation, three commands are available:

| Command | Entry Point |
|---------|-------------|
| `acm-switchover` | `acm_switchover:main` |
| `acm-switchover-rbac` | `check_rbac:main` |
| `acm-switchover-state` | `show_state:main` |

## Version

The version is read dynamically from `lib.__version__` at build time. To update:

```bash
./packaging/common/version-bump.sh 1.5.0
```

## Publishing to PyPI (Optional)

The repository includes an optional GitHub workflow for PyPI publishing using Trusted Publishing (OIDC):

1. Configure PyPI Trusted Publishing in your PyPI account
2. Enable the `.github/workflows/pypi-publish.yml` workflow
3. Tag a release: `git tag v1.5.0 && git push origin v1.5.0`

The workflow will automatically build and publish on tagged releases.
