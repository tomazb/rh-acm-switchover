# EE Base-Stage Python Pip Design

## Goal

Ensure the collection execution-environment definition installs the pip RPM for
its selected Python 3.12 interpreter before Ansible Builder attempts to bootstrap
or use pip in the base and builder stages.

## Scope

This change addresses only the remaining execution-environment build-order
defect. The syntax-check loop-body review item is already fixed at the current
repository `HEAD` and is intentionally left unchanged.

## Considered Approaches

1. Add `python3.12-pip` to
   `dependencies.python_interpreter.package_system` alongside `python3.12`.
   Ansible Builder expands this value into the base-stage package-manager command,
   so both packages exist before its pip bootstrap runs. This is the selected
   approach because it expresses both packages as one interpreter prerequisite
   and needs no custom build step.
2. Install `python3.12-pip` in `additional_build_steps.prepend_base`. This runs
   before the generated interpreter installation, obscures their relationship,
   and can install Python indirectly before the declared interpreter step.
3. Skip Ansible Builder's pip bootstrap and install pip in `append_base`. This
   requires more custom configuration and duplicates generated package-manager
   behavior without adding value.

## Design

Change `dependencies.python_interpreter.package_system` from `python3.12` to the
space-separated package set `python3.12 python3.12-pip`. Ansible Builder 3.1.1
quotes that value in `ARG PYPKG` and later runs `RUN $PKGMGR install $PYPKG -y`
in the base stage, allowing normal shell word splitting to pass both RPM names to
the package manager before `/output/scripts/pip_install $PYCMD` executes.

Remove `python3.12-pip` from `bindep.txt`; leaving it there would suggest that the
final-stage bindep installation is responsible for satisfying the earlier base
stage. Keep `python3.12` out of bindep for the same reason. The default
`python3`/`python3-pip` bindep requirements remain unchanged because they are
part of the collection's ordinary system dependency input.

Update the compatibility contract test to assert that both the interpreter RPM
and its matching pip RPM are declared in `python_interpreter.package_system`.
Also assert that the matching pip RPM is absent from bindep so a future change
cannot accidentally restore the same ordering defect while satisfying a mere
package-presence check.

Update the execution-environment compatibility documentation to state that both
RPMs are installed by the base-stage interpreter package command, before Ansible
Builder's pip bootstrap. This corrects the current build-order implication without
changing the collection's compatibility or parity status.

## Verification

Use test-driven development: first change the compatibility test and observe it
fail against the current YAML. Then update the YAML, bindep input, and documentation
and rerun the targeted collection compatibility test. Finally run the affected
root CI guardrail test, formatting checks for changed Python files, and diff
whitespace validation.
