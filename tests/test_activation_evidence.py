"""R4-04 PR C Task 7: the Python activation migration-evidence transaction.

A dict-backed fake destination hub serves the strict reads and the guarded Restore
mutations; a real StateManager persists the journal, so "durable before mutation" is
checked against the state file at the instant of each mutation.
"""

import copy
import json
from contextlib import contextmanager
from unittest.mock import patch

import pytest

import modules.activation as activation_module
from lib.constants import (
    BACKUP_NAMESPACE,
    CLUSTER_BACKUP_API_GROUP,
    MANAGED_CLUSTER_RESTORE_NAME,
    RESTORE_FULL_NAME,
    RESTORE_PASSIVE_SYNC_NAME,
)
from lib.exceptions import FatalError
from lib.run_record import RunRecord
from lib.strict_read import StrictReadOutcome
from lib.utils import StateManager

SecondaryActivation = activation_module.SecondaryActivation
NS = BACKUP_NAMESPACE
MC_F = "veleroManagedClustersBackupName"
CREDS_F = "veleroCredentialsBackupName"
RES_F = "veleroResourcesBackupName"
STAMP = "20240102120000"
START = "2024-01-02T12:00:00Z"
MC_B = f"acm-managed-clusters-schedule-{STAMP}"
CRED_B = f"acm-credentials-schedule-{STAMP}"
RES_B = f"acm-resources-schedule-{STAMP}"
GEN_B = f"acm-resources-generic-schedule-{STAMP}"


def backup(name, start=START, uid=None, phase="Completed", spec=None, **status):
    body = {
        "metadata": {"name": name, "namespace": NS, "uid": uid or f"uid-{name}", "resourceVersion": "7"},
        "status": {"phase": phase, "startTimestamp": start, "completionTimestamp": "2024-01-02T12:05:00Z", **status},
    }
    if spec is not None:
        body["spec"] = spec
    return body


def passive_restore(
    name=RESTORE_PASSIVE_SYNC_NAME,
    mc="skip",
    creds="latest",
    res="latest",
    sync=True,
    cleanup="CleanupRestored",
    status_names=None,
    uid="uid-passive",
    generation=3,
    phase="Enabled",
):
    status = {"phase": phase, "veleroCredentialsRestoreName": f"{name}-{CRED_B}", "veleroResourcesRestoreName": ""}
    status.update(status_names or {})
    return {
        "apiVersion": "cluster.open-cluster-management.io/v1beta1",
        "kind": "Restore",
        "metadata": {
            "name": name,
            "namespace": NS,
            "uid": uid,
            "resourceVersion": "1001",
            "generation": generation,
            "creationTimestamp": "2024-01-01T00:00:00Z",
        },
        "spec": {
            MC_F: mc,
            CREDS_F: creds,
            RES_F: res,
            "cleanupBeforeRestore": cleanup,
            "syncRestoreWithNewBackups": sync,
        },
        "status": status,
    }


class FakeHub:
    """The destination hub: strict reads and guarded mutations over in-memory objects."""

    def __init__(self, version="2.14.3", dry_run=False):
        self.dry_run = dry_run
        self.version = version
        self.acm = {}
        self.backups = {}
        self.velero = {}
        self.status_served = StrictReadOutcome.from_items([])
        self.failures = {}
        self.mutations = []
        self.journal_probe = lambda: None
        self.patch_override = None
        self.create_override = None
        self.after_mutation = None
        self._rv = 2000

    def add_backups(self, *raws):
        for raw in raws:
            self.backups[raw["metadata"]["name"]] = copy.deepcopy(raw)

    def _next_rv(self):
        self._rv += 1
        return str(self._rv)

    def _store(self, group, plural):
        if plural == "backups":
            return self.backups
        return self.acm if group == CLUSTER_BACKUP_API_GROUP else self.velero

    def list_custom_resources_strict(self, group, version, plural, namespace=None, label_selector=None):
        if f"list:{plural}" in self.failures:
            return self.failures[f"list:{plural}"]
        if plural == "multiclusterhubs":
            return StrictReadOutcome.from_items(
                [{"metadata": {"name": "hub"}, "status": {"currentVersion": self.version}}]
            )
        return StrictReadOutcome.from_items(copy.deepcopy(list(self._store(group, plural).values())), "1")

    def get_custom_resource_strict(self, group, version, plural, name, namespace=None):
        if f"get:{name}" in self.failures:
            return self.failures[f"get:{name}"]
        store = self._store(group, plural)
        if name not in store:
            return StrictReadOutcome.object_absent("object_not_found")
        obj = copy.deepcopy(store[name])
        return StrictReadOutcome.from_resource(obj, obj["metadata"].get("resourceVersion"))

    def api_resource_served(self, group, version, name):
        assert name == "restores/status"
        return self.status_served

    def list_custom_resources(self, **_kwargs):
        return []

    def create_custom_resource(self, group, version, plural, body, namespace=None):
        """The unguarded create the rollback uses to recreate the deleted passive sync Restore."""
        self.acm[body["metadata"]["name"]] = copy.deepcopy(body)
        return body

    @staticmethod
    def _result(**updates):
        result = {
            "accepted": False,
            "http_accepted": False,
            "changed": False,
            "would_change": False,
            "conflict": False,
            "dry_run": False,
            "reason": "unverifiable",
            "uid": None,
            "resource_version": None,
            "generation": None,
            "generation_reported": False,
        }
        result.update(updates)
        return result

    def _record(self, kind, name):
        self.mutations.append((kind, name, self.journal_probe()))

    def json_patch_custom_resource_guarded(
        self,
        name,
        *,
        namespace,
        uid,
        resource_version,
        expected_managed_clusters_backup_name,
        replacement_managed_clusters_backup_name,
        timeout_seconds=None,
    ):
        if self.dry_run:
            return self._result(dry_run=True, would_change=True, reason="predicted")
        self._record("patch", name)
        self.last_patch = dict(
            uid=uid, resource_version=resource_version, expected=expected_managed_clusters_backup_name
        )
        if self.patch_override is not None:
            return self.patch_override
        obj = self.acm.get(name)
        if obj is None:
            return self._result(reason="not_found")
        meta, spec = obj["metadata"], obj["spec"]
        if (meta["uid"], meta["resourceVersion"], spec[MC_F]) != (
            uid,
            resource_version,
            expected_managed_clusters_backup_name,
        ):
            return self._result(conflict=True, reason="precondition_failed")
        spec[MC_F] = replacement_managed_clusters_backup_name
        meta["generation"] += 1
        meta["resourceVersion"] = self._next_rv()
        result = self._result(
            accepted=True,
            http_accepted=True,
            changed=True,
            reason="ok",
            uid=uid,
            resource_version=meta["resourceVersion"],
            generation=meta["generation"],
            generation_reported=True,
        )
        if self.after_mutation:
            self.after_mutation(self)
        return result

    def delete_restore_guarded(self, name, *, namespace, uid, resource_version, timeout_seconds=None):
        if self.dry_run:
            return self._result(dry_run=True, would_change=True, reason="predicted")
        self._record("delete", name)
        obj = self.acm.get(name)
        if obj is None:
            return self._result(reason="not_found")
        if (obj["metadata"]["uid"], obj["metadata"]["resourceVersion"]) != (uid, resource_version):
            return self._result(conflict=True, reason="precondition_failed")
        del self.acm[name]
        return self._result(accepted=True, http_accepted=True, changed=True, reason="ok", uid=uid)

    def create_restore_guarded(self, body, *, namespace, timeout_seconds=None):
        if self.dry_run:
            return self._result(dry_run=True, would_change=True, reason="predicted")
        name = body["metadata"]["name"]
        self._record("create", name)
        self.last_create = copy.deepcopy(body)
        if self.create_override is not None:
            return self.create_override
        if name in self.acm:
            return self._result(conflict=True, reason="already_exists")
        obj = copy.deepcopy(body)
        obj["metadata"].update(uid=f"uid-{name}", resourceVersion=self._next_rv(), generation=1)
        self.acm[name] = obj
        result = self._result(
            accepted=True,
            http_accepted=True,
            changed=True,
            reason="ok",
            uid=obj["metadata"]["uid"],
            resource_version=obj["metadata"]["resourceVersion"],
            generation=1,
            generation_reported=True,
        )
        if self.after_mutation:
            self.after_mutation(self)
        return result


LEGACY_INVENTORY = [backup(MC_B), backup(CRED_B), backup(RES_B), backup(GEN_B)]


@pytest.fixture
def state(tmp_path):
    manager = StateManager(str(tmp_path / "state.json"))
    manager.flush_state()
    return manager


def _journal_on_disk(state):
    with open(state.state_file) as handle:
        return json.load(handle)["config"].get("migration_backups")


def _hub(state, *, version="2.14.3", inventory=LEGACY_INVENTORY, passive=True, dry_run=False):
    hub = FakeHub(version=version, dry_run=dry_run)
    hub.add_backups(*inventory)
    if passive:
        restore = passive_restore()
        hub.acm[restore["metadata"]["name"]] = restore
    hub.journal_probe = lambda: _journal_on_disk(state)
    return hub


def _activation(hub, state, kind):
    method, activation_method = {
        "passive_patch": ("passive", "patch"),
        "passive_restore": ("passive", "restore"),
        "full_restore": ("full", "patch"),
    }[kind]
    return SecondaryActivation(hub, state, method=method, activation_method=activation_method)


@contextmanager
def _no_wait():
    with patch.object(activation_module, "wait_for_condition", side_effect=lambda _d, poll, **_k: _drain(poll)):
        yield


def _drain(poll):
    for _ in range(3):
        if poll().complete:
            return True
    return False


# --- passive_patch -----------------------------------------------------------------------------


class TestPassivePatchFreezeAndPatch:
    def test_the_journal_is_durable_before_the_guarded_patch(self, state):
        hub = _hub(state)
        _activation(hub, state, "passive_patch")._apply_migration_mutation()

        [(kind, name, journal_at_patch)] = hub.mutations
        assert (kind, name) == ("patch", RESTORE_PASSIVE_SYNC_NAME)
        restore = journal_at_patch["restore"]
        assert restore["uid"] == "uid-passive" and restore["generation"] is None
        assert restore["mutation_kind"] == "passive_patch" and restore["acm_minor"] == "2.14"
        assert restore["backup_fields"] == {MC_F: "latest"}
        assert set(journal_at_patch["backups"]) == {
            "managed_clusters",
            "activation_credentials",
            "activation_resources",
            "activation_resources_generic",
        }
        assert restore["passive_patch_precondition"]["backup_fields_raw"] == {
            MC_F: "skip",
            CREDS_F: "latest",
            RES_F: "latest",
        }
        assert hub.last_patch == {"uid": "uid-passive", "resource_version": "1001", "expected": "skip"}
        journal = RunRecord(state).migration_backups()
        assert journal["restore"]["generation"] == 4
        assert hub.acm[RESTORE_PASSIVE_SYNC_NAME]["spec"][MC_F] == "latest"

    def test_2_17_freezes_the_same_four_categories(self, state):
        hub = _hub(state, version="2.17.0")
        _activation(hub, state, "passive_patch")._apply_migration_mutation()
        journal = RunRecord(state).migration_backups()
        assert journal["restore"]["controller_contract"] == "active_2_17"
        assert len(journal["backups"]) == 4

    def test_case_and_whitespace_follow_the_upstream_normalization(self, state):
        hub = _hub(state)
        hub.acm[RESTORE_PASSIVE_SYNC_NAME]["spec"].update({MC_F: " Skip ", CREDS_F: "LATEST"})
        _activation(hub, state, "passive_patch")._apply_migration_mutation()
        assert hub.last_patch["expected"] == " Skip "
        assert hub.acm[RESTORE_PASSIVE_SYNC_NAME]["spec"][CREDS_F] == "LATEST"

    @pytest.mark.parametrize(
        "changes",
        [
            pytest.param({"sync": False}, id="sync-false"),
            pytest.param({"mc": "latest"}, id="stale-latest"),
            pytest.param({"creds": "skip"}, id="credentials-skip"),
            pytest.param({"res": "acm-resources-schedule-x"}, id="resources-concrete"),
            pytest.param({"status_names": {"veleroManagedClustersRestoreName": "old"}}, id="mc-locator-set"),
            pytest.param({"cleanup": "None"}, id="cleanup-none"),
            pytest.param({"cleanup": "cleanuprestored"}, id="cleanup-not-exact"),
        ],
    )
    def test_a_fresh_precondition_failure_freezes_nothing_and_patches_nothing(self, state, changes):
        hub = _hub(state)
        hub.acm[RESTORE_PASSIVE_SYNC_NAME] = passive_restore(**changes)
        with pytest.raises(FatalError):
            _activation(hub, state, "passive_patch")._apply_migration_mutation()
        assert hub.mutations == []
        assert _journal_on_disk(state) is None

    @pytest.mark.parametrize("missing", [MC_B, CRED_B, RES_B, GEN_B])
    def test_an_indeterminate_auxiliary_input_blocks_before_intent(self, state, missing):
        hub = _hub(state, inventory=[raw for raw in LEGACY_INVENTORY if raw["metadata"]["name"] != missing])
        with pytest.raises(FatalError, match="required_prediction_missing"):
            _activation(hub, state, "passive_patch")._apply_migration_mutation()
        assert hub.mutations == [] and _journal_on_disk(state) is None

    def test_a_selected_partially_failed_backup_blocks(self, state):
        newer = backup("acm-resources-schedule-20240103120000", start="2024-01-03T12:00:00Z", phase="PartiallyFailed")
        hub = _hub(state, inventory=LEGACY_INVENTORY + [newer])
        with pytest.raises(FatalError, match="backup_not_completed"):
            _activation(hub, state, "passive_patch")._apply_migration_mutation()
        assert hub.mutations == [] and _journal_on_disk(state) is None

    @pytest.mark.parametrize(
        "outcome",
        [StrictReadOutcome.error("read_failed"), StrictReadOutcome.error("inventory_incomplete")],
    )
    def test_a_failed_restore_discovery_blocks_everything(self, state, outcome):
        hub = _hub(state)
        hub.failures["list:restores"] = outcome
        with pytest.raises(FatalError, match="discovery"):
            _activation(hub, state, "passive_patch")._apply_migration_mutation()
        assert hub.mutations == [] and _journal_on_disk(state) is None

    def test_a_failed_backup_inventory_blocks(self, state):
        hub = _hub(state)
        hub.failures["list:backups"] = StrictReadOutcome.crd_absent("kind_not_served")
        with pytest.raises(FatalError, match="Backup inventory"):
            _activation(hub, state, "passive_patch")._apply_migration_mutation()
        assert hub.mutations == [] and _journal_on_disk(state) is None

    @pytest.mark.parametrize(
        "served, match",
        [
            (StrictReadOutcome.crd_absent("kind_not_served"), "does not serve the status subresource"),
            (StrictReadOutcome.error("discovery_unverifiable"), "could not be verified"),
        ],
    )
    def test_the_restore_status_subresource_is_asserted_before_mutation(self, state, served, match):
        hub = _hub(state)
        hub.status_served = served
        with pytest.raises(FatalError, match=match):
            _activation(hub, state, "passive_patch")._apply_migration_mutation()
        assert hub.mutations == []

    def test_an_unknown_acm_minor_blocks(self, state):
        hub = _hub(state, version="2.18.0")
        with pytest.raises(FatalError, match="unknown_acm_minor"):
            _activation(hub, state, "passive_patch")._apply_migration_mutation()
        assert hub.mutations == [] and _journal_on_disk(state) is None

    def test_a_journal_write_failure_blocks_before_the_patch(self, state):
        hub = _hub(state)
        with patch.object(RunRecord, "record_migration_backups", side_effect=OSError("EIO")):
            with pytest.raises(FatalError, match="durably"):
                _activation(hub, state, "passive_patch")._apply_migration_mutation()
        assert hub.mutations == []


class TestPassivePatchBoundary:
    """The final pre-PATCH boundary re-proves everything from fresh strict reads."""

    def _frozen(self, state, **hub_kwargs):
        hub = _hub(state, **hub_kwargs)
        hub.patch_override = FakeHub._result(conflict=True, reason="precondition_failed")
        with pytest.raises(FatalError, match="not accepted"):
            _activation(hub, state, "passive_patch")._apply_migration_mutation()
        hub.patch_override = None
        hub.mutations.clear()
        return hub, RunRecord(state).migration_backups()

    def test_a_conflict_records_no_applied_evidence_and_a_retry_repatches(self, state):
        hub, journal = self._frozen(state)
        assert journal["restore"]["generation"] is None
        hub.acm[RESTORE_PASSIVE_SYNC_NAME]["metadata"]["resourceVersion"] = "1002"  # status-only activity
        _activation(hub, state, "passive_patch")._apply_migration_mutation()
        assert hub.last_patch["resource_version"] == "1002"
        after = RunRecord(state).migration_backups()
        assert after["restore"]["generation"] == 4
        assert after["backups"] == journal["backups"] and after["run_id"] == journal["run_id"]

    def test_resume_never_refreezes_to_a_newer_backup(self, state):
        hub, journal = self._frozen(state)
        hub.add_backups(backup("acm-managed-clusters-schedule-20240103120000", start="2024-01-03T12:00:00Z"))
        _activation(hub, state, "passive_patch")._apply_migration_mutation()
        assert [kind for kind, _, _ in hub.mutations] == ["patch"]
        after = RunRecord(state).migration_backups()
        assert after["backups"] == journal["backups"]
        assert after["backups"]["managed_clusters"]["name"] == MC_B

    def test_a_newer_credentials_backup_blocks_the_legacy_patch(self, state):
        hub, journal = self._frozen(state)
        hub.add_backups(backup("acm-credentials-schedule-20240103120000", start="2024-01-03T12:00:00Z"))
        with pytest.raises(FatalError, match="credential_variant_source_mismatch"):
            _activation(hub, state, "passive_patch")._apply_migration_mutation()
        assert hub.mutations == []
        assert RunRecord(state).migration_backups()["backups"] == journal["backups"]

    @pytest.mark.parametrize(
        "mutate",
        [
            pytest.param(lambda raw: raw["metadata"].update(uid="uid-replacement"), id="replaced-uid"),
            pytest.param(lambda raw: raw["status"].update(phase="Deleting"), id="phase-drift"),
            pytest.param(lambda raw: raw["status"].update(warnings=2), id="warnings-drift"),
            pytest.param(lambda raw: raw["status"].update(completionTimestamp="2024-01-02T12:06:00Z"), id="time"),
        ],
    )
    def test_a_replaced_or_drifted_frozen_backup_blocks_the_patch(self, state, mutate):
        hub, _ = self._frozen(state)
        mutate(hub.backups[CRED_B])
        with pytest.raises(FatalError, match="replaced or its status drifted|backup_not_completed"):
            _activation(hub, state, "passive_patch")._apply_migration_mutation()
        assert hub.mutations == []

    def test_omitted_zero_counters_are_the_same_evidence(self, state):
        hub, _ = self._frozen(state)
        hub.backups[CRED_B]["status"].update(errors=0, warnings=0)
        _activation(hub, state, "passive_patch")._apply_migration_mutation()
        assert [kind for kind, _, _ in hub.mutations] == ["patch"]

    def test_a_deleted_frozen_backup_blocks(self, state):
        hub, _ = self._frozen(state)
        del hub.backups[GEN_B]
        with pytest.raises(FatalError, match="could not be re-read"):
            _activation(hub, state, "passive_patch")._apply_migration_mutation()
        assert hub.mutations == []

    @pytest.mark.parametrize(
        "mutate",
        [
            pytest.param(lambda raw: raw["spec"].update(cleanupBeforeRestore="None"), id="cleanup"),
            pytest.param(lambda raw: raw["metadata"].update(uid="uid-replacement"), id="replaced-restore"),
            pytest.param(lambda raw: raw["metadata"].update(generation=9), id="generation"),
            pytest.param(lambda raw: raw["status"].update(veleroResourcesRestoreName="new"), id="status-names"),
            pytest.param(lambda raw: raw["spec"].update(veleroManagedClustersBackupName="latest"), id="stale-latest"),
        ],
    )
    def test_a_live_restore_that_changed_since_the_freeze_is_never_patched(self, state, mutate):
        hub, _ = self._frozen(state)
        mutate(hub.acm[RESTORE_PASSIVE_SYNC_NAME])
        with pytest.raises(FatalError):
            _activation(hub, state, "passive_patch")._apply_migration_mutation()
        assert hub.mutations == []
        assert RunRecord(state).migration_backups()["restore"]["generation"] is None

    def test_a_changed_acm_minor_blocks_resume(self, state):
        hub, _ = self._frozen(state)
        hub.version = "2.15.1"
        with pytest.raises(FatalError, match="ACM 2.14.*ACM 2.15"):
            _activation(hub, state, "passive_patch")._apply_migration_mutation()
        assert hub.mutations == []

    def test_a_different_activation_method_blocks_resume(self, state):
        hub, _ = self._frozen(state)
        with pytest.raises(FatalError, match="passive_patch transaction"):
            _activation(hub, state, "passive_restore")._apply_migration_mutation()
        assert hub.mutations == []


class TestLegacyCredentialVariants:
    """PR C decisions amendment section 1.2 rule 1 at the final pre-PATCH boundary."""

    def test_the_orlabelselectors_shortcut_permits_the_patch(self, state):
        shortcut = backup(CRED_B, spec={"orLabelSelectors": [{"matchLabels": {"a": "b"}}]})
        inventory = [raw for raw in LEGACY_INVENTORY if raw["metadata"]["name"] != CRED_B] + [shortcut]
        hub = _hub(state, inventory=inventory + [backup(f"acm-credentials-hive-schedule-{STAMP}")])
        _activation(hub, state, "passive_patch")._apply_migration_mutation()
        assert [kind for kind, _, _ in hub.mutations] == ["patch"]

    @pytest.mark.parametrize(
        "variant",
        [
            backup(f"acm-credentials-hive-schedule-{STAMP}"),
            backup("acm-credentials-cluster-schedule-20240102120010", start="2024-01-02T12:00:10Z"),
        ],
    )
    def test_a_dedicated_candidate_issues_zero_patch(self, state, variant):
        hub = _hub(state, inventory=LEGACY_INVENTORY + [variant])
        with pytest.raises(FatalError, match="legacy_credential_variant_selected"):
            _activation(hub, state, "passive_patch")._apply_migration_mutation()
        assert hub.mutations == []
        journal = RunRecord(state).migration_backups()
        assert journal is not None and set(journal["backups"]) == {
            "managed_clusters",
            "activation_credentials",
            "activation_resources",
            "activation_resources_generic",
        }

    def test_2_17_has_no_variant_requests(self, state):
        hub = _hub(
            state, version="2.17.2", inventory=LEGACY_INVENTORY + [backup(f"acm-credentials-hive-schedule-{STAMP}")]
        )
        _activation(hub, state, "passive_patch")._apply_migration_mutation()
        assert [kind for kind, _, _ in hub.mutations] == ["patch"]


class TestPassivePatchAcceptance:
    """Never infer acceptance: only an accepted response plus a strict post-read binds the generation."""

    @pytest.mark.parametrize(
        "result",
        [
            FakeHub._result(conflict=True, reason="precondition_failed"),
            FakeHub._result(reason="unverifiable"),
            FakeHub._result(http_accepted=True, reason="malformed_response"),
            FakeHub._result(reason="not_found"),
        ],
    )
    def test_a_non_accepted_patch_records_no_generation(self, state, result):
        hub = _hub(state)
        hub.patch_override = result
        with pytest.raises(FatalError, match="not accepted"):
            _activation(hub, state, "passive_patch")._apply_migration_mutation()
        assert RunRecord(state).migration_backups()["restore"]["generation"] is None

    def test_a_missing_response_generation_is_bound_from_the_uid_matched_reread(self, state):
        hub = _hub(state)
        original = hub.json_patch_custom_resource_guarded

        def patch_without_generation(*args, **kwargs):
            result = original(*args, **kwargs)
            return dict(result, generation=None, generation_reported=False)

        hub.json_patch_custom_resource_guarded = patch_without_generation
        _activation(hub, state, "passive_patch")._apply_migration_mutation()
        assert RunRecord(state).migration_backups()["restore"]["generation"] == 4

    @pytest.mark.parametrize(
        "after",
        [
            pytest.param(lambda fake: fake.acm[RESTORE_PASSIVE_SYNC_NAME]["metadata"].update(uid="u2"), id="uid"),
            pytest.param(lambda fake: fake.acm[RESTORE_PASSIVE_SYNC_NAME]["metadata"].update(generation=7), id="gen"),
            pytest.param(
                lambda fake: fake.acm[RESTORE_PASSIVE_SYNC_NAME]["spec"].update(cleanupBeforeRestore="None"),
                id="cleanup",
            ),
            pytest.param(
                lambda fake: fake.acm[RESTORE_PASSIVE_SYNC_NAME]["spec"].update({CREDS_F: "skip"}), id="owned"
            ),
            pytest.param(lambda fake: fake.acm.pop(RESTORE_PASSIVE_SYNC_NAME), id="absent"),
        ],
    )
    def test_the_strict_post_patch_read_must_bind(self, state, after):
        hub = _hub(state)
        hub.after_mutation = after
        with pytest.raises(FatalError):
            _activation(hub, state, "passive_patch")._apply_migration_mutation()
        assert RunRecord(state).migration_backups()["restore"]["generation"] is None

    def test_an_accepted_patch_is_reconciled_on_resume_without_a_second_patch(self, state):
        hub = _hub(state)
        _activation(hub, state, "passive_patch")._apply_migration_mutation()
        hub.mutations.clear()
        _activation(hub, state, "passive_patch")._apply_migration_mutation()
        _activation(hub, state, "passive_patch")._reconcile_migration_mutation()
        assert hub.mutations == []
        hub.acm[RESTORE_PASSIVE_SYNC_NAME]["spec"]["cleanupBeforeRestore"] = "None"
        with pytest.raises(FatalError, match="cleanupBeforeRestore"):
            _activation(hub, state, "passive_patch")._reconcile_migration_mutation()


def test_a_completed_mutation_step_without_a_journal_blocks(state):
    hub = _hub(state)
    with pytest.raises(FatalError, match="no migration journal"):
        _activation(hub, state, "passive_patch")._reconcile_migration_mutation()


def test_a_passive_patch_name_collision_issues_zero_patch(state):
    hub = _hub(state, passive=False)
    long_name = "r" * 250
    hub.acm[long_name] = passive_restore(name=long_name)
    with pytest.raises(FatalError, match="generated_name_collision"):
        _activation(hub, state, "passive_patch")._apply_migration_mutation()
    assert hub.mutations == []


# --- one-shot creates ----------------------------------------------------------------------------


class TestOneShotCreates:
    def test_passive_restore_journals_before_the_delete_and_creates_latest_skip_skip(self, state):
        hub = _hub(state)
        with _no_wait():
            _activation(hub, state, "passive_restore")._apply_migration_mutation()
        assert [(kind, name) for kind, name, _ in hub.mutations] == [
            ("delete", RESTORE_PASSIVE_SYNC_NAME),
            ("create", MANAGED_CLUSTER_RESTORE_NAME),
        ]
        journal_at_delete = hub.mutations[0][2]
        assert journal_at_delete["restore"]["uid"] is None
        assert set(journal_at_delete["backups"]) == {
            "managed_clusters",
            "activation_credentials",
            "activation_resources_generic",
        }
        assert hub.last_create["spec"] == {
            MC_F: "latest",
            CREDS_F: "skip",
            RES_F: "skip",
            "cleanupBeforeRestore": "CleanupRestored",
        }
        journal = RunRecord(state).migration_backups()
        assert journal["restore"]["uid"] == f"uid-{MANAGED_CLUSTER_RESTORE_NAME}"
        assert journal["restore"]["generation"] == 1
        assert journal["restore"]["backup_fields"] == {MC_F: "latest"}

    def test_passive_restore_on_2_17_freezes_only_managed_clusters(self, state):
        hub = _hub(state, version="2.17.0")
        with _no_wait():
            _activation(hub, state, "passive_restore")._apply_migration_mutation()
        assert set(RunRecord(state).migration_backups()["backups"]) == {"managed_clusters"}

    def test_full_restore_names_concrete_backups_of_one_generation(self, state):
        hub = _hub(state, passive=False)
        _activation(hub, state, "full_restore")._apply_migration_mutation()
        assert hub.last_create["spec"] == {
            MC_F: MC_B,
            CREDS_F: CRED_B,
            RES_F: RES_B,
            "cleanupBeforeRestore": "CleanupRestored",
        }
        journal = RunRecord(state).migration_backups()
        assert journal["restore"]["backup_fields"] == {MC_F: MC_B, CREDS_F: CRED_B, RES_F: RES_B}
        assert journal["backups"]["resources_generic"]["name"] == GEN_B

    def test_full_restore_blocks_on_mixed_generations(self, state):
        mixed = LEGACY_INVENTORY + [backup("acm-credentials-schedule-20240102130000", start="2024-01-02T13:00:00Z")]
        hub = _hub(state, passive=False, inventory=mixed)
        with pytest.raises(FatalError, match="mixed_backup_generation"):
            _activation(hub, state, "full_restore")._apply_migration_mutation()
        assert hub.mutations == [] and _journal_on_disk(state) is None

    def test_legacy_full_restore_blocks_on_a_hive_candidate(self, state):
        hub = _hub(
            state, passive=False, inventory=LEGACY_INVENTORY + [backup(f"acm-credentials-hive-schedule-{STAMP}")]
        )
        with pytest.raises(FatalError, match="legacy_credential_variant_selected"):
            _activation(hub, state, "full_restore")._apply_migration_mutation()
        assert hub.mutations == [] and _journal_on_disk(state) is None

    @pytest.mark.parametrize(
        "kind, name", [("passive_restore", MANAGED_CLUSTER_RESTORE_NAME), ("full_restore", RESTORE_FULL_NAME)]
    )
    def test_an_unowned_existing_restore_is_never_adopted(self, state, kind, name):
        hub = _hub(state)
        hub.acm[name] = passive_restore(name=name, sync=False, uid="uid-foreign")
        with pytest.raises(FatalError, match="never adopted"):
            _activation(hub, state, kind)._apply_migration_mutation()
        assert hub.mutations == [] and _journal_on_disk(state) is None

    @pytest.mark.parametrize(
        "result",
        [
            FakeHub._result(conflict=True, reason="already_exists"),
            FakeHub._result(reason="unverifiable"),
            FakeHub._result(http_accepted=True, reason="malformed_response"),
        ],
    )
    def test_a_non_accepted_create_records_no_identity_and_rolls_back(self, state, result):
        hub = _hub(state)
        hub.create_override = result
        with _no_wait(), pytest.raises(FatalError, match="not accepted.*Recreated passive sync restore"):
            _activation(hub, state, "passive_restore")._apply_migration_mutation()
        journal = RunRecord(state).migration_backups()
        assert journal["restore"]["uid"] is None and journal["restore"]["generation"] is None

    @pytest.mark.parametrize(
        "after",
        [
            pytest.param(lambda fake: fake.acm[RESTORE_FULL_NAME]["metadata"].update(uid="u2"), id="uid"),
            pytest.param(
                lambda fake: fake.acm[RESTORE_FULL_NAME]["spec"].update(cleanupBeforeRestore="None"), id="cleanup"
            ),
            pytest.param(lambda fake: fake.acm[RESTORE_FULL_NAME]["spec"].update({CREDS_F: "latest"}), id="latest"),
            pytest.param(lambda fake: fake.acm[RESTORE_FULL_NAME]["metadata"].update(generation=2), id="generation"),
        ],
    )
    def test_the_strict_post_create_read_must_bind(self, state, after):
        hub = _hub(state, passive=False)
        hub.after_mutation = after
        with pytest.raises(FatalError):
            _activation(hub, state, "full_restore")._apply_migration_mutation()
        assert RunRecord(state).migration_backups()["restore"]["uid"] is None

    def test_a_created_restore_is_reconciled_on_resume(self, state):
        hub = _hub(state, passive=False)
        _activation(hub, state, "full_restore")._apply_migration_mutation()
        hub.mutations.clear()
        _activation(hub, state, "full_restore")._apply_migration_mutation()
        assert hub.mutations == []
        hub.acm[RESTORE_FULL_NAME]["metadata"]["uid"] = "uid-replacement"
        with pytest.raises(FatalError, match="never adopted"):
            _activation(hub, state, "full_restore")._reconcile_migration_mutation()

    def test_the_passive_delete_is_guarded_by_uid_and_resource_version(self, state):
        hub = _hub(state)
        hub.acm[RESTORE_PASSIVE_SYNC_NAME]["metadata"]["resourceVersion"] = "1500"
        original = hub.delete_restore_guarded
        seen = {}

        def delete(name, **kwargs):
            seen.update(kwargs)
            return original(name, **kwargs)

        hub.delete_restore_guarded = delete
        with _no_wait():
            _activation(hub, state, "passive_restore")._apply_migration_mutation()
        assert (seen["uid"], seen["resource_version"]) == ("uid-passive", "1500")

    def test_a_one_shot_name_collision_blocks_before_the_journal(self, state):
        hub = _hub(state, passive=False)
        with patch.object(activation_module, "RESTORE_FULL_NAME", "r" * 250):
            with pytest.raises(FatalError, match="generated_name_collision"):
                _activation(hub, state, "full_restore")._apply_migration_mutation()
        assert hub.mutations == [] and _journal_on_disk(state) is None


# --- dry-run -----------------------------------------------------------------------------------


@pytest.mark.parametrize("kind", ["passive_patch", "passive_restore", "full_restore"])
def test_dry_run_reads_and_predicts_but_writes_nothing(state, kind):
    hub = _hub(state, dry_run=True, passive=kind != "full_restore")
    before = open(state.state_file, "rb").read()
    _activation(hub, state, kind)._apply_migration_mutation()
    assert hub.mutations == []
    assert open(state.state_file, "rb").read() == before


# --- Thermos R2-M2 readiness and rollback, on the journaled paths ----------------------------------


@pytest.mark.parametrize("kind", ["passive_patch", "passive_restore"])
def test_a_degraded_passive_restore_is_never_mutated(state, kind):
    hub = _hub(state)
    hub.acm[RESTORE_PASSIVE_SYNC_NAME]["status"].update(phase="Error", lastMessage="sync broke")
    with _no_wait(), pytest.raises(FatalError, match="Passive sync restore not ready: Error"):
        _activation(hub, state, kind)._apply_migration_mutation()
    assert hub.mutations == []


def test_a_patched_restore_is_reconciled_without_revalidating_readiness(state):
    hub = _hub(state)
    _activation(hub, state, "passive_patch")._apply_migration_mutation()
    hub.acm[RESTORE_PASSIVE_SYNC_NAME]["status"].update(phase="FinishedWithErrors", lastMessage="post-patch churn")
    hub.mutations.clear()
    _activation(hub, state, "passive_patch")._reconcile_migration_mutation()
    assert hub.mutations == []


def test_a_failed_rollback_is_reported_with_the_create_failure(state):
    hub = _hub(state)
    hub.create_override = FakeHub._result(reason="unverifiable")

    def refuse(*_args, **_kwargs):
        raise RuntimeError("rollback recreate failed")

    hub.create_custom_resource = refuse
    with _no_wait(), pytest.raises(FatalError, match="Rollback failed to recreate passive sync restore"):
        _activation(hub, state, "passive_restore")._apply_migration_mutation()
