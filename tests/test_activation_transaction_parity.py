"""Parity contract: the R4-04 activation transaction decides identically in both form factors.

The Python CLI's SecondaryActivation and the collection's acm_migration_evidence decisions
(module_utils/migration_activation.py, driven in the order roles/activation/tasks runs
them) run the same scenario on two identical fake destination hubs. Each invocation's
outcome, the Restore mutations with the journal durable at each of them, the persisted
journal and the final hub state must agree. Generated identifiers and timestamps are
pinned. The two runtimes share no code; the YAML wiring itself is covered by the
collection's integration tests against a fake API.
"""

import copy
import uuid
from contextlib import ExitStack
from unittest.mock import patch

import pytest

import modules.activation as activation_module
import tests.test_activation_evidence as ev
from ansible_collections.tomazb.acm_switchover.plugins.module_utils import checkpoint as col_checkpoint
from ansible_collections.tomazb.acm_switchover.plugins.module_utils import migration_activation as col
from lib.constants import CLUSTER_BACKUP_API_GROUP, RESTORE_PASSIVE_SYNC_NAME
from lib.exceptions import FatalError
from lib.run_record import RunRecord
from lib.strict_read import StrictReadOutcome, StrictReadStatus
from lib.utils import StateManager

RUN_ID = "0f8a1c2e-3b4d-4e5f-8a9b-0c1d2e3f4a5b"
RESOLVED_AT = "2026-10-01T00:00:00+00:00"
_POLLS = 3  # tests.test_activation_evidence._drain polls the deletion wait three times
_VELERO = ("velero.io", "v1")
_ACM = (CLUSTER_BACKUP_API_GROUP, "v1beta1")


def _read(outcome: StrictReadOutcome) -> dict:
    """The acm_k8s_read_outcome result the collection publishes for the same strict read."""
    if outcome.is_success:
        resources = [outcome.resource] if outcome.resource is not None else list(outcome.items)
        return {"read_status": "ok", "resources": copy.deepcopy(resources), "resource_version": "1"}
    status = {
        StrictReadStatus.OBJECT_ABSENT: "not_found",
        StrictReadStatus.CRD_ABSENT: "kind_not_served",
    }.get(outcome.status, "error")
    return {"read_status": status, "resources": [], "resource_version": None}


class CollectionActivation:
    """One activation invocation of the collection, mirroring roles/activation/tasks."""

    def __init__(self, hub, store):
        self.hub = hub
        self.store = store  # the checkpoint: {"operational_data": {...}}

    def _get(self, plural, name, api=_ACM):
        return _read(self.hub.get_custom_resource_strict(api[0], api[1], plural, name, namespace=ev.NS))

    def _list(self, plural, api=_ACM, namespace=ev.NS):
        return _read(self.hub.list_custom_resources_strict(api[0], api[1], plural, namespace=namespace))

    def _backup_reads(self, journal):
        return [self._get("backups", entry["name"], _VELERO) for entry in col.frozen_backup_names(journal)]

    def _persist(self, candidate, preview):
        if not preview:
            col_checkpoint.record_migration_backups(self.store, candidate)

    def run(self, kind, *, preview=False):
        hub = self.hub
        mch = _read(hub.list_custom_resources_strict("operator.open-cluster-management.io", "v1", "multiclusterhubs"))
        minor = col.acm_minor(mch)["acm_minor"]
        journal = col_checkpoint.migration_backups(self.store)
        if kind != "full_restore" and journal is None:
            restores = self._list("restores")
            discovered = col.find_passive_sync_restore(restores)
            live = self._get("restores", discovered["metadata"]["name"]) if discovered else {}
            col.verify_passive_sync(restores, live)
        if journal is None:
            inputs = {"kind": kind, "acm_minor": minor, "run_id": RUN_ID, "resolved_at": RESOLVED_AT}
            if kind == "passive_patch":
                inputs["restores_read"] = self._list("restores")
                discovered = col.find_passive_sync_restore(inputs["restores_read"])
                if discovered is not None:
                    inputs["live_read"] = self._get("restores", discovered["metadata"]["name"])
            else:
                inputs["target_read"] = self._get("restores", col.target_restore_name(kind))
            inputs["inventory_read"] = self._list("backups", _VELERO)
            journal = col.freeze(**inputs)["candidate"]
            self._persist(journal, preview)
        col.require_journal_matches_run(journal, kind, minor)
        served = hub.api_resource_served(CLUSTER_BACKUP_API_GROUP, "v1beta1", "restores/status")
        col.require_status_subresource(_read(served) if not served.is_success else {"read_status": "ok"})
        restore = journal["restore"]
        pending = restore["generation"] is None if kind == "passive_patch" else restore["uid"] is None
        if not pending:
            col.reconcile(
                journal=journal,
                live_read=self._get("restores", restore["name"]),
                backup_reads=self._backup_reads(journal),
            )
        elif kind == "passive_patch":
            self._patch(journal, preview)
        else:
            self._create(journal, preview)

    def _patch(self, journal, preview):
        name = journal["restore"]["name"]
        live = self._get("restores", name)
        backups = self._backup_reads(journal)
        inventory = self._list("backups", _VELERO)
        args = col.pre_patch(journal=journal, live_read=live, backup_reads=backups, inventory_read=inventory)
        result = self.hub.json_patch_custom_resource_guarded(
            name,
            namespace=ev.NS,
            uid=args["uid"],
            resource_version=args["resource_version"],
            expected_managed_clusters_backup_name=args["expected_managed_clusters_backup_name"],
            replacement_managed_clusters_backup_name="latest",
        )
        if not preview:
            candidate = col.post_patch(journal=journal, result=result, post_read=self._get("restores", name))[
                "candidate"
            ]
            self._persist(candidate, preview)

    def _create(self, journal, preview):
        name = journal["restore"]["name"]
        decision = col.pre_create(
            journal=journal,
            target_read=self._get("restores", name),
            backup_reads=self._backup_reads(journal),
            restores_read=self._list("restores"),
        )
        deleted = None
        if decision["passive"] is not None:
            passive = decision["passive"]
            target = col.pre_delete(discovered=passive, live_read=self._get("restores", passive["metadata"]["name"]))
            if target["action"] == "delete":
                result = self.hub.delete_restore_guarded(
                    target["name"], namespace=ev.NS, uid=target["uid"], resource_version=target["resource_version"]
                )
                if not preview:
                    col.post_delete(name=target["name"], result=result)
                    for _ in range(_POLLS):
                        poll = col.deletion_poll(
                            name=target["name"], uid=target["uid"], live_read=self._get("restores", target["name"])
                        )
                        if poll["absent"]:
                            break
                    else:
                        raise col.ActivationRefused(f"Timeout waiting for deletion of restore {target['name']}")
                    deleted = target["name"]
        body = col.create_body(journal=journal, backup_reads=self._backup_reads(journal))["body"]
        result = self.hub.create_restore_guarded(body, namespace=ev.NS)
        if not preview:
            candidate = col.post_create(
                journal=journal, result=result, post_read=self._get("restores", name), deleted_passive=deleted
            )["candidate"]
            self._persist(candidate, preview)


class Forms:
    """The two form factors, each over its own identical fake hub."""

    def __init__(self, tmp_path, **hub_kwargs):
        self.state = StateManager(str(tmp_path / "state.json"))
        self.state.flush_state()
        self.py_hub = ev._hub(self.state, **hub_kwargs)
        self.store = {"operational_data": {}}
        self.col_hub = ev._hub(self.state, **hub_kwargs)
        self.col_hub.journal_probe = lambda: copy.deepcopy(
            self.store["operational_data"].get(col_checkpoint.KEY_MIGRATION_BACKUPS)
        )

    def both(self, change):
        """Apply the same change to both hubs."""
        change(self.py_hub)
        change(self.col_hub)

    def _python(self, kind, preview):
        activation = ev._activation(self.py_hub, self.state, kind)
        self.py_hub.dry_run = preview
        with ExitStack() as stack:
            stack.enter_context(patch.object(activation_module.uuid, "uuid4", return_value=uuid.UUID(RUN_ID)))
            stack.enter_context(patch.object(activation_module, "_now_iso", return_value=RESOLVED_AT))
            stack.enter_context(ev._no_wait())
            if kind != "full_restore" and RunRecord(self.state).migration_backups() is None:
                activation._verify_passive_sync()
            activation._apply_migration_mutation()

    def run(self, kind, *, preview=False):
        outcomes = []
        for form, call in (("python", lambda: self._python(kind, preview)), ("collection", None)):
            if call is None:
                self.col_hub.dry_run = preview
                call = lambda: CollectionActivation(self.col_hub, self.store).run(kind, preview=preview)  # noqa: E731
            try:
                call()
                outcomes.append(None)
            except (FatalError, col.ActivationRefused) as exc:
                outcomes.append(str(exc))
        return outcomes

    def assert_same(self, outcomes, *, compare_message=True):
        python, collection = outcomes
        assert (python is None) == (collection is None), outcomes
        if compare_message:
            assert python == collection
        assert RunRecord(self.state).migration_backups() == col_checkpoint.migration_backups(self.store)
        assert self.py_hub.mutations == self.col_hub.mutations
        assert self.py_hub.acm == self.col_hub.acm

    def clear_mutations(self):
        self.py_hub.mutations.clear()
        self.col_hub.mutations.clear()


KINDS = ["passive_patch", "passive_restore", "full_restore"]
LANES = [pytest.param("2.14.3", id="legacy"), pytest.param("2.17.1", id="2.17")]


@pytest.mark.parametrize("version", LANES)
@pytest.mark.parametrize("kind", KINDS)
def test_a_fresh_transaction_freezes_and_mutates_identically(tmp_path, kind, version):
    forms = Forms(tmp_path, version=version)
    outcomes = forms.run(kind)
    forms.assert_same(outcomes)
    assert outcomes == [None, None]
    assert forms.col_hub.mutations, "the transaction mutated nothing"
    # Every mutation happened with the journal already durable.
    assert all(journal is not None for _, _, journal in forms.col_hub.mutations)


@pytest.mark.parametrize("kind", KINDS)
def test_a_rerun_only_reconciles_identically(tmp_path, kind):
    forms = Forms(tmp_path)
    forms.assert_same(forms.run(kind))
    forms.clear_mutations()
    outcomes = forms.run(kind)
    forms.assert_same(outcomes)
    assert outcomes == [None, None] and forms.col_hub.mutations == []


@pytest.mark.parametrize("kind", KINDS)
def test_a_preview_reads_and_predicts_but_mutates_and_journals_nothing(tmp_path, kind):
    forms = Forms(tmp_path)
    outcomes = forms.run(kind, preview=True)
    forms.assert_same(outcomes)
    assert outcomes == [None, None]
    assert forms.col_hub.mutations == [] and col_checkpoint.migration_backups(forms.store) is None


FRESH_PATCH_REFUSALS = [
    pytest.param({"sync": False}, id="sync-false"),
    pytest.param({"mc": "latest"}, id="stale-latest"),
    pytest.param({"creds": "skip"}, id="credentials-skip"),
    pytest.param({"res": "acm-resources-schedule-x"}, id="resources-concrete"),
    pytest.param({"status_names": {"veleroManagedClustersRestoreName": "old"}}, id="mc-locator-set"),
    pytest.param({"cleanup": "None"}, id="cleanup-none"),
    pytest.param({"cleanup": "cleanuprestored"}, id="cleanup-not-exact"),
    pytest.param({"phase": "Failed"}, id="degraded"),
]


@pytest.mark.parametrize("changes", FRESH_PATCH_REFUSALS)
def test_a_fresh_precondition_refusal_freezes_and_patches_nothing(tmp_path, changes):
    forms = Forms(tmp_path)
    forms.both(lambda hub: hub.acm.__setitem__(RESTORE_PASSIVE_SYNC_NAME, ev.passive_restore(**changes)))
    outcomes = forms.run("passive_patch")
    forms.assert_same(outcomes)
    assert outcomes[0] is not None and forms.col_hub.mutations == []


@pytest.mark.parametrize("kind", KINDS)
@pytest.mark.parametrize("missing", [ev.MC_B, ev.CRED_B, ev.RES_B, ev.GEN_B])
def test_an_indeterminate_prediction_blocks_before_intent(tmp_path, kind, missing):
    inventory = [raw for raw in ev.LEGACY_INVENTORY if raw["metadata"]["name"] != missing]
    forms = Forms(tmp_path, inventory=inventory)
    forms.assert_same(forms.run(kind))


@pytest.mark.parametrize("kind", KINDS)
@pytest.mark.parametrize(
    "extra",
    [
        pytest.param(
            [ev.backup("acm-resources-schedule-20240103120000", start="2024-01-03T12:00:00Z", phase="PartiallyFailed")],
            id="partially-failed-newest",
        ),
        pytest.param(
            [ev.backup("acm-managed-clusters-schedule-20240103120000", start="2024-01-03T12:00:00Z")],
            id="mixed-generation",
        ),
        pytest.param([ev.backup(f"acm-credentials-hive-schedule-{ev.STAMP}")], id="hive-candidate"),
        pytest.param([ev.backup("acm-managed-clusters-schedule-20240102115959", start=ev.START)], id="latest-tie"),
    ],
)
def test_selection_refusals_and_admissions_agree(tmp_path, kind, extra):
    forms = Forms(tmp_path, inventory=ev.LEGACY_INVENTORY + extra)
    forms.assert_same(forms.run(kind))


@pytest.mark.parametrize("kind", KINDS)
@pytest.mark.parametrize(
    "failure",
    [
        pytest.param(("list:restores", StrictReadOutcome.error("read_failed")), id="restore-list"),
        pytest.param(("list:backups", StrictReadOutcome.crd_absent("kind_not_served")), id="backup-list"),
        pytest.param(("list:multiclusterhubs", StrictReadOutcome.error("read_failed")), id="mch"),
        pytest.param(("get:restore-acm-activate", StrictReadOutcome.error("read_failed")), id="activate-get"),
        pytest.param(("get:restore-acm-full", StrictReadOutcome.error("read_failed")), id="full-get"),
        pytest.param((f"get:{RESTORE_PASSIVE_SYNC_NAME}", StrictReadOutcome.error("read_failed")), id="passive-get"),
    ],
)
def test_a_failed_strict_read_blocks_identically(tmp_path, kind, failure):
    forms = Forms(tmp_path)
    forms.both(lambda hub: hub.failures.__setitem__(*failure))
    outcomes = forms.run(kind)
    # The read status wording differs by form factor; the decision does not.
    forms.assert_same(outcomes, compare_message=False)


@pytest.mark.parametrize("kind", KINDS)
@pytest.mark.parametrize(
    "served",
    [StrictReadOutcome.crd_absent("kind_not_served"), StrictReadOutcome.error("discovery_unverifiable")],
    ids=["absent", "unverifiable"],
)
def test_the_status_subresource_is_required_identically(tmp_path, kind, served):
    forms = Forms(tmp_path)
    forms.both(lambda hub: setattr(hub, "status_served", served))
    outcomes = forms.run(kind)
    forms.assert_same(outcomes, compare_message=False)
    assert outcomes[0] is not None and forms.col_hub.mutations == []


@pytest.mark.parametrize("kind", KINDS)
def test_an_unknown_acm_minor_blocks_identically(tmp_path, kind):
    forms = Forms(tmp_path, version="2.18.0")
    forms.assert_same(forms.run(kind))


def _frozen_patch(tmp_path):
    forms = Forms(tmp_path)
    forms.both(
        lambda hub: setattr(hub, "patch_override", ev.FakeHub._result(conflict=True, reason="precondition_failed"))
    )
    forms.assert_same(forms.run("passive_patch"))
    forms.both(lambda hub: setattr(hub, "patch_override", None))
    forms.clear_mutations()
    return forms


PATCH_BOUNDARY_CHANGES = [
    pytest.param(lambda hub: hub.acm[RESTORE_PASSIVE_SYNC_NAME]["metadata"].update(resourceVersion="1002"), id="rv"),
    pytest.param(
        lambda hub: hub.add_backups(
            ev.backup("acm-managed-clusters-schedule-20240103120000", start="2024-01-03T12:00:00Z")
        ),
        id="newer-mc",
    ),
    pytest.param(
        lambda hub: hub.add_backups(ev.backup("acm-credentials-schedule-20240103120000", start="2024-01-03T12:00:00Z")),
        id="newer-credentials",
    ),
    pytest.param(lambda hub: hub.backups[ev.CRED_B]["metadata"].update(uid="uid-replacement"), id="replaced-backup"),
    pytest.param(lambda hub: hub.backups[ev.CRED_B]["status"].update(warnings=2), id="warnings-drift"),
    pytest.param(lambda hub: hub.backups[ev.CRED_B]["status"].update(errors=0, warnings=0), id="omitted-counters"),
    pytest.param(lambda hub: hub.backups.pop(ev.GEN_B), id="deleted-backup"),
    pytest.param(lambda hub: hub.acm[RESTORE_PASSIVE_SYNC_NAME]["spec"].update(cleanupBeforeRestore="None"), id="cl"),
    pytest.param(lambda hub: hub.acm[RESTORE_PASSIVE_SYNC_NAME]["metadata"].update(uid="uid-x"), id="replaced-restore"),
    pytest.param(lambda hub: hub.acm[RESTORE_PASSIVE_SYNC_NAME]["metadata"].update(generation=9), id="generation"),
    pytest.param(
        lambda hub: hub.acm[RESTORE_PASSIVE_SYNC_NAME]["status"].update(veleroResourcesRestoreName="new"), id="status"
    ),
    pytest.param(lambda hub: hub.acm[RESTORE_PASSIVE_SYNC_NAME]["spec"].update({ev.MC_F: "latest"}), id="stale"),
    pytest.param(lambda hub: setattr(hub, "version", "2.15.1"), id="minor-changed"),
]


@pytest.mark.parametrize("change", PATCH_BOUNDARY_CHANGES)
def test_the_final_pre_patch_boundary_decides_identically(tmp_path, request, change):
    forms = _frozen_patch(tmp_path)
    forms.both(change)
    outcomes = forms.run("passive_patch")
    # A deleted Backup's strict GET status is worded differently by each form factor.
    forms.assert_same(outcomes, compare_message=request.node.callspec.id != "deleted-backup")


def test_a_different_activation_method_on_resume_blocks_identically(tmp_path):
    forms = _frozen_patch(tmp_path)
    forms.assert_same(forms.run("passive_restore"))


@pytest.mark.parametrize(
    "result",
    [
        ev.FakeHub._result(conflict=True, reason="precondition_failed"),
        ev.FakeHub._result(reason="unverifiable"),
        ev.FakeHub._result(http_accepted=True, reason="malformed_response"),
        ev.FakeHub._result(reason="not_found"),
    ],
    ids=["conflict", "unverifiable", "malformed", "not-found"],
)
def test_a_non_accepted_patch_binds_nothing_identically(tmp_path, result):
    forms = Forms(tmp_path)
    forms.both(lambda hub: setattr(hub, "patch_override", result))
    outcomes = forms.run("passive_patch")
    forms.assert_same(outcomes)
    assert col_checkpoint.migration_backups(forms.store)["restore"]["generation"] is None


@pytest.mark.parametrize(
    "after",
    [
        pytest.param(lambda hub: hub.acm[RESTORE_PASSIVE_SYNC_NAME]["metadata"].update(uid="u2"), id="uid"),
        pytest.param(lambda hub: hub.acm[RESTORE_PASSIVE_SYNC_NAME]["metadata"].update(generation=7), id="gen"),
        pytest.param(
            lambda hub: hub.acm[RESTORE_PASSIVE_SYNC_NAME]["spec"].update(cleanupBeforeRestore="None"), id="cleanup"
        ),
        pytest.param(lambda hub: hub.acm[RESTORE_PASSIVE_SYNC_NAME]["spec"].update({ev.CREDS_F: "skip"}), id="owned"),
        pytest.param(lambda hub: hub.acm.pop(RESTORE_PASSIVE_SYNC_NAME), id="absent"),
    ],
)
def test_the_post_patch_read_binds_identically(tmp_path, after):
    forms = Forms(tmp_path)
    forms.both(lambda hub: setattr(hub, "after_mutation", after))
    forms.assert_same(forms.run("passive_patch"))


def test_a_patch_response_without_generation_binds_the_reread_identically(tmp_path):
    forms = Forms(tmp_path)

    def strip(hub):
        original = hub.json_patch_custom_resource_guarded
        hub.json_patch_custom_resource_guarded = lambda *a, **k: dict(
            original(*a, **k), generation=None, generation_reported=False
        )

    forms.both(strip)
    outcomes = forms.run("passive_patch")
    forms.assert_same(outcomes)
    assert col_checkpoint.migration_backups(forms.store)["restore"]["generation"] == 4


@pytest.mark.parametrize(
    "kind, name", [("passive_restore", "restore-acm-activate"), ("full_restore", "restore-acm-full")]
)
def test_an_unowned_existing_one_shot_restore_is_never_adopted_identically(tmp_path, kind, name):
    forms = Forms(tmp_path)
    forms.both(lambda hub: hub.acm.__setitem__(name, ev.passive_restore(name=name, sync=False, uid="uid-foreign")))
    forms.assert_same(forms.run(kind))


@pytest.mark.parametrize("kind", ["passive_restore", "full_restore"])
@pytest.mark.parametrize(
    "result",
    [
        ev.FakeHub._result(conflict=True, reason="already_exists"),
        ev.FakeHub._result(reason="unverifiable"),
        ev.FakeHub._result(http_accepted=True, reason="malformed_response"),
        ev.FakeHub._result(accepted=True, http_accepted=True, changed=True, reason="ok", uid="u-1"),
    ],
    ids=["already-exists", "unverifiable", "malformed", "no-generation"],
)
def test_a_non_accepted_create_is_never_rolled_back_or_retried_identically(tmp_path, kind, result):
    forms = Forms(tmp_path)
    forms.both(lambda hub: setattr(hub, "create_override", result))
    outcomes = forms.run(kind)
    forms.assert_same(outcomes)
    # The passive sync Restore stays deleted: no automatic recreate.
    assert RESTORE_PASSIVE_SYNC_NAME not in forms.col_hub.acm
    assert [mutation for mutation, _, _ in forms.col_hub.mutations] == ["delete", "create"]
    forms.both(lambda hub: setattr(hub, "create_override", None))
    forms.clear_mutations()
    forms.assert_same(forms.run(kind))


@pytest.mark.parametrize("kind", ["passive_restore", "full_restore"])
@pytest.mark.parametrize(
    "after",
    [
        pytest.param(lambda name: lambda hub: hub.acm[name]["metadata"].update(uid="u2"), id="uid"),
        pytest.param(lambda name: lambda hub: hub.acm[name]["metadata"].update(generation=3), id="gen"),
        pytest.param(lambda name: lambda hub: hub.acm[name]["spec"].update(cleanupBeforeRestore="None"), id="cl"),
        pytest.param(lambda name: lambda hub: hub.acm.pop(name), id="absent"),
    ],
)
def test_the_post_create_read_binds_identically(tmp_path, kind, after):
    name = col.target_restore_name(kind)
    forms = Forms(tmp_path)
    forms.both(lambda hub: setattr(hub, "after_mutation", after(name)))
    forms.assert_same(forms.run(kind))


@pytest.mark.parametrize("kind", ["passive_restore", "full_restore"])
def test_a_replacement_passive_restore_is_never_deleted_identically(tmp_path, kind):
    forms = Forms(tmp_path)

    def replace_on_read(hub):
        replaced = {"done": False}

        def controller(fake, name):
            if name == RESTORE_PASSIVE_SYNC_NAME and fake.mutations == [] and fake.journal_probe() is not None:
                if not replaced["done"]:
                    replaced["done"] = True
                    fake.acm[name]["metadata"]["uid"] = "uid-replacement"

        hub.controller = controller

    forms.both(replace_on_read)
    outcomes = forms.run(kind)
    forms.assert_same(outcomes)
    assert outcomes[0] is not None and forms.col_hub.mutations == []


def test_a_passive_patch_name_collision_issues_zero_patch_identically(tmp_path):
    forms = Forms(tmp_path, passive=False)
    long_name = "r" * 250
    forms.both(lambda hub: hub.acm.__setitem__(long_name, ev.passive_restore(name=long_name)))
    outcomes = forms.run("passive_patch")
    forms.assert_same(outcomes)
    assert outcomes[0] is not None and forms.col_hub.mutations == []
