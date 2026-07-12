"""Shared semantic strategies for future property-based test suites.

Future suites will generate valid-shaped domain objects and targeted
near-valid mutations for validation, path safety, checkpoint/resume, report
artifacts, BackupSchedule, Argo CD, and RBAC behavior. Broad arbitrary
dictionaries or byte blobs are not the default strategy pattern because they
rarely reach meaningful domain behavior.

Strategies are intentionally added only with the PBT-03 through PBT-09 suite
that first needs them; this module currently defines no public strategy API.
"""
