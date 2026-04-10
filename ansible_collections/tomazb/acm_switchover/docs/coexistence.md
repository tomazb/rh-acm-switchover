# Coexistence with the Python Tool

## Shared Behavior Contract

Parity is tracked by shared scenarios and the parity matrix, not by internal implementation shape.

## Dual-Bug-Fix Policy

Safety and correctness defects in dual-supported features must be evaluated for both implementations.

## Shared Code Policy

- share behavior specs, schemas, fixtures, and sample artifacts where useful
- do not share live runtime orchestration code by default
- prefer disciplined duplication over accidental coupling when execution models differ

## Discovery Bridge

`scripts/discover-hub.sh` remains the supported discovery bridge during coexistence.
Its output must be documented in terms of:

- `acm_switchover_hubs.primary.context`
- `acm_switchover_hubs.secondary.context`
- optional kubeconfig path inputs

## Checkpoint State Translation

The Python tool and the collection use separate checkpoint file formats.  They are
**not interchangeable** at runtime.

| Scenario | Guidance |
| --- | --- |
| Start with Python, finish with collection | Not supported. Begin a fresh collection run. |
| Start with collection, inspect with Python | Read the JSON checkpoint file directly; no Python helper supports it. |
| Migrate checkpoint between runs | Use the collection checkpoint JSON as-is. |

When a collection checkpoint exists at `acm_switchover_execution.checkpoint.path`, the
`checkpoint_phase` action plugin skips any phase listed in `completed_phases` on resume.
A fresh run (or `checkpoint.reset: true`) starts from the beginning regardless of any
pre-existing checkpoint file.
