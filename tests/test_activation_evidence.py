"""R4-04 PR C Task 7: the Python activation migration-evidence transaction.

A dict-backed fake destination hub serves the strict reads and the guarded Restore
mutations; a real StateManager persists the journal, so "durable before mutation" is
checked against the state file at the instant of each mutation.
"""

import copy
import json
import logging
from contextlib import ExitStack, contextmanager
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

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
from lib.kube_client import KubeClient
from lib.run_record import RunRecord
from lib.strict_read import StrictReadOutcome
from lib.utils import Phase, StateManager

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
        self.managed_clusters = ["cluster-a"]
        self.controller = None
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
        if self.controller is not None and group == CLUSTER_BACKUP_API_GROUP:
            self.controller(self, name)
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

    def list_managed_clusters_strict(self):
        if "list:managedclusters" in self.failures:
            return self.failures["list:managedclusters"]
        names = self.managed_clusters + ["local-cluster"]
        return StrictReadOutcome.from_items([{"metadata": {"name": name}} for name in names])

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
        if poll().done:
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

    @pytest.mark.parametrize("selectors", [[{"matchLabels": {"a": "b"}}], []], ids=["shortcut", "no-candidate"])
    def test_a_credentials_backup_replaced_before_the_final_list_issues_zero_patch(self, state, selectors):
        """Codex pre-PR finding 1: the strict GET still sees the frozen UID, the final LIST a replacement."""
        hub, journal = self._frozen(state)
        replacement = backup(CRED_B, uid="uid-replacement", spec={"orLabelSelectors": selectors})
        items = [raw for name, raw in hub.backups.items() if name != CRED_B] + [replacement]
        hub.failures["list:backups"] = StrictReadOutcome.from_items(copy.deepcopy(items), "1")
        with pytest.raises(FatalError, match="credential_variant_source_mismatch"):
            _activation(hub, state, "passive_patch")._apply_migration_mutation()
        assert hub.mutations == []
        assert RunRecord(state).migration_backups() == journal

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

    @staticmethod
    def _through_the_kube_client(hub, response_metadata):
        """Serve the fake's accepted PATCH response through the real KubeClient response parsing."""
        original = hub.json_patch_custom_resource_guarded

        def patch_through_kube_client(name, **kwargs):
            result = original(name, **kwargs)
            if not result["accepted"]:
                return result
            metadata = {k: v for k, v in hub.acm[name]["metadata"].items() if k != "generation"}
            body = {"kind": "Restore", "metadata": dict(metadata, **response_metadata)}
            with ExitStack() as stack:
                for target in ("config.new_client_from_config", "config.load_kube_config", "client.CustomObjectsApi"):
                    stack.enter_context(patch(f"lib.kube_client.{target}"))
                client = KubeClient(context="secondary-hub", dry_run=False)
            client._api_client.call_api = Mock(return_value=Mock(data=json.dumps(body).encode("utf-8")))
            return client.json_patch_custom_resource_guarded(name, **kwargs)

        hub.json_patch_custom_resource_guarded = patch_through_kube_client

    @pytest.mark.parametrize("generation", [True, "2"], ids=["boolean", "string"])
    def test_a_malformed_response_generation_records_no_applied_evidence(self, state, generation):
        """Codex pre-PR finding 3: a present invalid generation is never the omitted-generation fallback."""
        hub = _hub(state)
        self._through_the_kube_client(hub, {"generation": generation})
        with pytest.raises(FatalError, match=r"not accepted \(malformed_response\)"):
            _activation(hub, state, "passive_patch")._apply_migration_mutation()
        assert RunRecord(state).migration_backups()["restore"]["generation"] is None
        assert _journal_on_disk(state)["restore"]["generation"] is None

    @pytest.mark.parametrize("response_metadata", [{}, {"generation": 4}], ids=["omitted", "integer"])
    def test_an_omitted_or_integer_response_generation_binds_through_the_kube_client(self, state, response_metadata):
        hub = _hub(state)
        self._through_the_kube_client(hub, response_metadata)
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


def velero_child(name, backup_name, owner, owner_uid, phase="Completed", uid=None):
    return {
        "apiVersion": "velero.io/v1",
        "kind": "Restore",
        "metadata": {
            "name": name,
            "namespace": NS,
            "uid": uid or f"uid-{name}",
            "ownerReferences": [
                {
                    "apiVersion": "cluster.open-cluster-management.io/v1beta1",
                    "kind": "Restore",
                    "name": owner,
                    "uid": owner_uid,
                    "controller": True,
                }
            ],
        },
        "spec": {"backupName": backup_name},
        "status": {"phase": phase},
    }


def child_name(owner, backup_name, active=False):
    return f"{owner}-{backup_name}"[:252] + ("-active" if active else "")


def complete(hub, name, *, version, kind, phase=None, child_phase="Completed", overrides=None):
    """Play the controller: publish the lane's children and status for the journaled Restore."""
    restore = hub.acm[name]
    owner_uid = restore["metadata"]["uid"]
    legacy = not version.startswith("2.17")
    children = {}
    status = {}
    if kind == "passive_patch":
        children["mc"] = (child_name(name, MC_B), MC_B)
        children["cred_active"] = (child_name(name, CRED_B, True), CRED_B)
        children["gen_active"] = (child_name(name, GEN_B, True), GEN_B)
        children["cred_shared"] = (child_name(name, CRED_B), CRED_B)
        status["veleroManagedClustersRestoreName"] = children["mc"][0]
        if legacy:
            status["veleroCredentialsRestoreName"] = children["cred_active"][0]
            status["veleroGenericResourcesRestoreName"] = children["gen_active"][0]
        phase = phase or "Enabled"
    else:
        children["mc"] = (child_name(name, MC_B), MC_B)
        status["veleroManagedClustersRestoreName"] = children["mc"][0]
        if kind == "full_restore" or legacy:
            children["cred"] = (child_name(name, CRED_B), CRED_B)
            children["gen"] = (child_name(name, GEN_B), GEN_B)
            status["veleroCredentialsRestoreName"] = children["cred"][0]
            status["veleroGenericResourcesRestoreName"] = children["gen"][0]
        if kind == "full_restore":
            children["res"] = (child_name(name, RES_B), RES_B)
            status["veleroResourcesRestoreName"] = children["res"][0]
            if not legacy:
                children["cred_active"] = (child_name(name, CRED_B, True), CRED_B)
                children["gen_active"] = (child_name(name, GEN_B, True), GEN_B)
        phase = phase or "Finished"
    children.update(overrides or {})
    for role, (child, backup_name) in children.items():
        hub.velero[child] = velero_child(child, backup_name, name, owner_uid, phase=child_phase)
    restore.setdefault("status", {}).update(status, phase=phase)
    return children


RESTORE_NAMES = {
    "passive_patch": RESTORE_PASSIVE_SYNC_NAME,
    "passive_restore": MANAGED_CLUSTER_RESTORE_NAME,
    "full_restore": RESTORE_FULL_NAME,
}
LANES = [pytest.param("2.14.3", id="legacy"), pytest.param("2.17.1", id="2.17")]
KINDS = ["passive_patch", "passive_restore", "full_restore"]


def _mutated(state, kind, version="2.14.3", waiver=None, expected=("cluster-a",)):
    """A hub and an activation whose journaled Restore mutation is accepted."""
    hub = _hub(state, version=version, passive=kind != "full_restore")
    activation = SecondaryActivation(
        hub,
        state,
        method="full" if kind == "full_restore" else "passive",
        activation_method="restore" if kind == "passive_restore" else "patch",
        expected_managed_cluster_names=list(expected),
        enforce_expected_managed_cluster_names=bool(expected),
        managed_cluster_expectation_waiver=waiver,
    )
    with _no_wait():
        activation._apply_migration_mutation()
    return hub, activation


def _wait(activation):
    with _no_wait():
        activation._wait_for_restore_completion()


class TestCompletionEvidence:
    @pytest.mark.parametrize("version", LANES)
    @pytest.mark.parametrize("kind", KINDS)
    def test_completion_is_recorded_with_completed_at_strictly_last(self, state, kind, version):
        hub, activation = _mutated(state, kind, version)
        complete(hub, RESTORE_NAMES[kind], version=version, kind=kind)
        writes = []
        original = RunRecord.record_migration_backups

        def record(record_self, candidate):
            writes.append(copy.deepcopy(candidate["restore"]))
            return original(record_self, candidate)

        with patch.object(RunRecord, "record_migration_backups", autospec=True, side_effect=record):
            _wait(activation)
        assert [write["completed_at"] is None for write in writes] == [True, False]
        bundle, final = writes
        assert bundle["spec_fingerprint"] and bundle["backup_names_verified_at"] and bundle["names_verified_at"]
        assert {key: value for key, value in final.items() if key != "completed_at"} == {
            key: value for key, value in bundle.items() if key != "completed_at"
        }
        journal = RunRecord(state).migration_backups()
        assert journal["restore"]["velero_restores"]["managed_clusters"][0]["backup_name"] == MC_B

    def test_legacy_passive_patch_journals_the_shared_credentials_child(self, state):
        hub, activation = _mutated(state, "passive_patch")
        complete(hub, RESTORE_PASSIVE_SYNC_NAME, version="2.14.3", kind="passive_patch")
        _wait(activation)
        credentials = RunRecord(state).migration_backups()["restore"]["velero_restores"]["activation_credentials"]
        assert [entry["name"] for entry in credentials] == [
            child_name(RESTORE_PASSIVE_SYNC_NAME, CRED_B),
            child_name(RESTORE_PASSIVE_SYNC_NAME, CRED_B, True),
        ]

    def test_pending_polls_complete_once_the_controller_publishes(self, state):
        hub, activation = _mutated(state, "full_restore")
        calls = {"n": 0}

        def controller(fake, name):
            calls["n"] += 1
            if calls["n"] == 3:
                complete(fake, RESTORE_FULL_NAME, version="2.14.3", kind="full_restore")

        hub.controller = controller
        _wait(activation)
        assert RunRecord(state).migration_backups()["restore"]["completed_at"] is not None

    def test_a_running_child_keeps_the_wait_pending_until_timeout(self, state):
        hub, activation = _mutated(state, "full_restore")
        complete(hub, RESTORE_FULL_NAME, version="2.14.3", kind="full_restore", child_phase="InProgress")
        with pytest.raises(FatalError, match="Timeout"):
            _wait(activation)
        assert RunRecord(state).migration_backups()["restore"]["spec_fingerprint"] is None

    @pytest.mark.parametrize(
        "kind, version, phase",
        [
            ("passive_patch", "2.17.1", "EnabledWithErrors"),
            ("passive_patch", "2.14.3", "FinishedWithErrors"),
            ("passive_restore", "2.14.3", "FinishedWithErrors"),
            ("full_restore", "2.17.1", "FinishedWithErrors"),
            ("full_restore", "2.14.3", "Enabled"),
            ("full_restore", "2.14.3", "Error"),
            ("full_restore", "2.14.3", "Completed"),
        ],
    )
    def test_a_non_accepted_acm_phase_writes_no_evidence(self, state, kind, version, phase):
        hub, activation = _mutated(state, kind, version)
        complete(hub, RESTORE_NAMES[kind], version=version, kind=kind, phase=phase)
        hub.acm[RESTORE_NAMES[kind]]["status"]["messages"] = ["ManagedCluster prod1 already available"]
        with pytest.raises(FatalError, match="reached phase"):
            _wait(activation)
        assert RunRecord(state).migration_backups()["restore"]["spec_fingerprint"] is None

    @pytest.mark.parametrize("child_phase", ["Failed", "PartiallyFailed", "FailedValidation"])
    def test_a_failed_child_writes_no_evidence(self, state, child_phase):
        hub, activation = _mutated(state, "passive_restore")
        complete(hub, MANAGED_CLUSTER_RESTORE_NAME, version="2.14.3", kind="passive_restore", child_phase=child_phase)
        with pytest.raises(FatalError, match="velero_restore_not_completed"):
            _wait(activation)
        assert RunRecord(state).migration_backups()["restore"]["spec_fingerprint"] is None

    def test_a_child_that_consumed_a_newer_backup_blocks(self, state):
        hub, activation = _mutated(state, "passive_patch")
        newer = "acm-managed-clusters-schedule-20240103120000"
        raced = child_name(RESTORE_PASSIVE_SYNC_NAME, newer)
        complete(
            hub, RESTORE_PASSIVE_SYNC_NAME, version="2.14.3", kind="passive_patch", overrides={"mc": (raced, newer)}
        )
        hub.acm[RESTORE_PASSIVE_SYNC_NAME]["status"]["veleroManagedClustersRestoreName"] = raced
        with pytest.raises(FatalError, match="velero_restore_backup_mismatch"):
            _wait(activation)
        assert RunRecord(state).migration_backups()["restore"]["completed_at"] is None

    @pytest.mark.parametrize("child_phase", ["Completed", "Failed"])
    def test_a_historical_hive_child_blocks_legacy_completion(self, state, child_phase):
        hub, activation = _mutated(state, "passive_patch")
        complete(hub, RESTORE_PASSIVE_SYNC_NAME, version="2.14.3", kind="passive_patch")
        hive = f"acm-credentials-hive-schedule-{STAMP}"
        name = child_name(RESTORE_PASSIVE_SYNC_NAME, hive)
        hub.velero[name] = velero_child(name, hive, RESTORE_PASSIVE_SYNC_NAME, "uid-passive", phase=child_phase)
        with pytest.raises(FatalError, match="credential_variant_child|velero_restore_not_completed"):
            _wait(activation)
        assert RunRecord(state).migration_backups()["restore"]["completed_at"] is None

    def test_an_owner_child_of_an_unfrozen_backup_blocks_one_shot_completion(self, state):
        hub, activation = _mutated(state, "passive_restore", "2.17.1")
        complete(hub, MANAGED_CLUSTER_RESTORE_NAME, version="2.17.1", kind="passive_restore")
        extra = child_name(MANAGED_CLUSTER_RESTORE_NAME, CRED_B)
        hub.velero[extra] = velero_child(
            extra, CRED_B, MANAGED_CLUSTER_RESTORE_NAME, f"uid-{MANAGED_CLUSTER_RESTORE_NAME}"
        )
        with pytest.raises(FatalError, match="owner_child_unfrozen_backup"):
            _wait(activation)

    def test_a_missing_expected_name_blocks_before_any_evidence(self, state):
        hub, activation = _mutated(state, "passive_patch", expected=("cluster-a", "cluster-b"))
        complete(hub, RESTORE_PASSIVE_SYNC_NAME, version="2.14.3", kind="passive_patch")
        with pytest.raises(FatalError, match="Missing expected ManagedCluster"):
            _wait(activation)
        assert RunRecord(state).migration_backups()["restore"]["spec_fingerprint"] is None

    def test_an_activation_waiver_is_journaled_and_never_verifies_names(self, state):
        waiver = {"scope": "activation", "actor": "ops@example.com", "reason": "stale list", "request_id": None}
        hub, activation = _mutated(state, "passive_patch", waiver=waiver, expected=("cluster-a", "cluster-b"))
        complete(hub, RESTORE_PASSIVE_SYNC_NAME, version="2.14.3", kind="passive_patch")
        _wait(activation)
        journal = RunRecord(state).migration_backups()
        assert journal["restore"]["names_verified_at"] is None
        assert journal["restore"]["completed_at"] is not None
        assert journal["waiver"]["outcome"] == "waived" and journal["waiver"]["scope"] == "activation"
        assert journal["waiver"]["flag"] == "--skip-managed-cluster-expectations"

    def test_a_waiver_never_lifts_the_count_floor(self, state):
        waiver = {"scope": "both", "actor": "ops@example.com", "reason": "stale list", "request_id": None}
        hub, activation = _mutated(state, "passive_patch", waiver=waiver, expected=("cluster-a", "cluster-b"))
        activation.min_managed_clusters = 2
        complete(hub, RESTORE_PASSIVE_SYNC_NAME, version="2.14.3", kind="passive_patch")
        with pytest.raises(FatalError, match="at least 2"):
            _wait(activation)
        assert RunRecord(state).migration_backups()["restore"]["completed_at"] is None

    def test_a_post_activation_waiver_does_not_cover_activation(self, state):
        waiver = {"scope": "post_activation", "actor": "ops@example.com", "reason": "stale", "request_id": None}
        hub, activation = _mutated(state, "passive_patch", waiver=waiver, expected=("cluster-a", "cluster-b"))
        complete(hub, RESTORE_PASSIVE_SYNC_NAME, version="2.14.3", kind="passive_patch")
        with pytest.raises(FatalError, match="Missing expected ManagedCluster"):
            _wait(activation)

    @pytest.mark.parametrize(
        "failure, match",
        [
            ("list:restores", "Velero Restore list"),
            ("list:managedclusters", "ManagedCluster inventory"),
        ],
    )
    def test_a_failed_strict_read_writes_no_evidence(self, state, failure, match):
        hub, activation = _mutated(state, "full_restore")
        complete(hub, RESTORE_FULL_NAME, version="2.14.3", kind="full_restore")
        # The ACM Restore list is served from the same key; fail only the Velero read.
        if failure == "list:restores":
            original = hub.list_custom_resources_strict

            def velero_fails(group, version, plural, namespace=None, label_selector=None):
                if group == "velero.io":
                    return StrictReadOutcome.error("read_failed")
                return original(group, version, plural, namespace, label_selector)

            hub.list_custom_resources_strict = velero_fails
        else:
            hub.failures[failure] = StrictReadOutcome.error("read_failed")
        with pytest.raises(FatalError, match=match):
            _wait(activation)
        assert RunRecord(state).migration_backups()["restore"]["spec_fingerprint"] is None


class TestCompletionResume:
    def _completed(self, state, kind="passive_patch", version="2.14.3"):
        hub, activation = _mutated(state, kind, version)
        complete(hub, RESTORE_NAMES[kind], version=version, kind=kind)
        _wait(activation)
        return hub, RunRecord(state).migration_backups()

    def test_an_interrupted_bundle_is_reconciled_and_completed_last(self, state):
        hub, activation = _mutated(state, "full_restore")
        complete(hub, RESTORE_FULL_NAME, version="2.14.3", kind="full_restore")
        original = RunRecord.record_migration_backups
        calls = {"n": 0}

        def fail_second(record_self, candidate):
            calls["n"] += 1
            if calls["n"] == 2:
                raise OSError("EIO")
            return original(record_self, candidate)

        with patch.object(RunRecord, "record_migration_backups", autospec=True, side_effect=fail_second):
            with pytest.raises(FatalError, match="durably"):
                _wait(activation)
        partial = RunRecord(state).migration_backups()
        assert partial["restore"]["spec_fingerprint"] and partial["restore"]["completed_at"] is None

        _wait(_activation(hub, state, "full_restore"))
        final = RunRecord(state).migration_backups()["restore"]
        assert final["completed_at"] is not None
        for key in ("spec_fingerprint", "backup_names_verified_at", "names_verified_at", "velero_restores"):
            assert final[key] == partial["restore"][key]

    def test_a_partial_bundle_that_disagrees_with_live_children_blocks(self, state):
        hub, activation = _mutated(state, "full_restore")
        children = complete(hub, RESTORE_FULL_NAME, version="2.14.3", kind="full_restore")
        original = RunRecord.record_migration_backups
        calls = {"n": 0}

        def fail_second(record_self, candidate):
            calls["n"] += 1
            if calls["n"] == 2:
                raise OSError("EIO")
            return original(record_self, candidate)

        with patch.object(RunRecord, "record_migration_backups", autospec=True, side_effect=fail_second):
            with pytest.raises(FatalError):
                _wait(activation)
        hub.velero[children["res"][0]]["metadata"]["uid"] = "uid-replaced"
        with pytest.raises(FatalError, match="disagrees"):
            _wait(_activation(hub, state, "full_restore"))
        assert RunRecord(state).migration_backups()["restore"]["completed_at"] is None

    def test_completed_evidence_is_revalidated_without_a_rewrite(self, state):
        hub, journal = self._completed(state)
        before = open(state.state_file, "rb").read()
        _wait(_activation(hub, state, "passive_patch"))
        _activation(hub, state, "passive_patch")._reconcile_restore_completion()
        assert open(state.state_file, "rb").read() == before

    @pytest.mark.parametrize(
        "mutate, match",
        [
            pytest.param(
                lambda hub: hub.velero[child_name(RESTORE_PASSIVE_SYNC_NAME, MC_B)]["metadata"].update(uid="u2"),
                "replaced or changed",
                id="child-uid",
            ),
            pytest.param(
                lambda hub: hub.velero.pop(child_name(RESTORE_PASSIVE_SYNC_NAME, MC_B)),
                "required_child_missing",
                id="gone",
            ),
            pytest.param(
                lambda hub: hub.acm[RESTORE_PASSIVE_SYNC_NAME]["spec"].update(cleanupBeforeRestore="None"),
                "cleanupBeforeRestore",
                id="cleanup",
            ),
            pytest.param(
                lambda hub: hub.acm[RESTORE_PASSIVE_SYNC_NAME]["metadata"].update(generation=9),
                "generation drift",
                id="generation",
            ),
            pytest.param(
                lambda hub: hub.velero.update(
                    {
                        "hive": velero_child(
                            "hive",
                            f"acm-credentials-hive-schedule-{STAMP}",
                            RESTORE_PASSIVE_SYNC_NAME,
                            "uid-passive",
                        )
                    }
                ),
                "credential_variant_child",
                id="later-hive-child",
            ),
        ],
    )
    def test_completed_evidence_drift_blocks_resume(self, state, mutate, match):
        hub, journal = self._completed(state)
        mutate(hub)
        with pytest.raises(FatalError, match=match):
            _wait(_activation(hub, state, "passive_patch"))
        assert RunRecord(state).migration_backups() == journal

    def test_a_completed_wait_marker_without_completion_evidence_blocks(self, state):
        hub, activation = _mutated(state, "full_restore")
        with pytest.raises(FatalError, match="no completion evidence"):
            activation._reconcile_restore_completion()

    def test_a_different_requested_waiver_blocks_resume(self, state):
        waiver = {"scope": "activation", "actor": "ops@example.com", "reason": "stale list", "request_id": None}
        hub, activation = _mutated(state, "full_restore", waiver=waiver, expected=("cluster-a", "cluster-z"))
        children = complete(hub, RESTORE_FULL_NAME, version="2.14.3", kind="full_restore")
        original = RunRecord.record_migration_backups
        calls = {"n": 0}

        def fail_second(record_self, candidate):
            calls["n"] += 1
            if calls["n"] == 2:
                raise OSError("EIO")
            return original(record_self, candidate)

        with patch.object(RunRecord, "record_migration_backups", autospec=True, side_effect=fail_second):
            with pytest.raises(FatalError):
                _wait(activation)
        assert children
        other = dict(waiver, actor="someone-else")
        resumed = SecondaryActivation(
            hub,
            state,
            method="full",
            expected_managed_cluster_names=["cluster-a", "cluster-z"],
            enforce_expected_managed_cluster_names=True,
            managed_cluster_expectation_waiver=other,
        )
        with pytest.raises(FatalError, match="different expected-ManagedCluster waiver"):
            _wait(resumed)


def test_dry_run_skips_the_completion_wait(state):
    hub = _hub(state, dry_run=True)
    activation = _activation(hub, state, "passive_patch")
    activation._wait_for_restore_completion()
    activation._reconcile_restore_completion()


@pytest.mark.parametrize("kind", KINDS)
def test_activate_runs_the_transaction_and_a_rerun_only_reconciles(state, kind):
    hub = _hub(state, passive=kind != "full_restore")
    hub.after_mutation = lambda fake: complete(fake, RESTORE_NAMES[kind], version="2.14.3", kind=kind)
    activation = SecondaryActivation(
        hub,
        state,
        method="full" if kind == "full_restore" else "passive",
        activation_method="restore" if kind == "passive_restore" else "patch",
        expected_managed_cluster_names=["cluster-a"],
        enforce_expected_managed_cluster_names=True,
    )
    with _no_wait():
        assert activation.activate() is True
    journal = RunRecord(state).migration_backups()
    assert journal["restore"]["completed_at"] is not None
    mutations = len(hub.mutations)
    with _no_wait():
        assert _activation(hub, state, kind).activate() is True
    assert len(hub.mutations) == mutations
    assert RunRecord(state).migration_backups() == journal


def test_a_child_not_yet_listed_keeps_the_wait_pending(state):
    hub, activation = _mutated(state, "full_restore")
    children = complete(hub, RESTORE_FULL_NAME, version="2.14.3", kind="full_restore")
    held = hub.velero.pop(children["gen"][0])
    calls = {"n": 0}

    def controller(fake, name):
        calls["n"] += 1
        if calls["n"] == 3:
            fake.velero[held["metadata"]["name"]] = held

    hub.controller = controller
    _wait(activation)
    assert RunRecord(state).migration_backups()["restore"]["completed_at"] is not None


def test_activate_blocks_a_completed_wait_marker_without_completion_evidence(state):
    hub, activation = _mutated(state, "full_restore")
    state.mark_step_completed("create_full_restore")
    state.mark_step_completed("wait_restore_completion")
    with _no_wait():
        assert _activation(hub, state, "full_restore").activate() is False
    assert "no completion evidence" in state.get_errors()[-1]["error"]


def test_following_the_restore_completion_guidance_establishes_the_evidence(state):
    """Codex T12 B1: a finalization resume refused for restore_not_completed recovers as the refusal says.

    Setting current_phase alone reaches the completed wait marker and refuses again; also
    removing only wait_restore_completion lets the retry prove completion from fresh reads.
    """
    from lib.workflow import run_phase_flow

    hub, _ = _mutated(state, "full_restore")
    complete(hub, RESTORE_FULL_NAME, version="2.14.3", kind="full_restore")
    for step in ("create_full_restore", "wait_restore_completion", "apply_immediate_import_annotations"):
        state.mark_step_completed(step)
    state.set_phase(Phase.FINALIZATION)
    failed = []
    run_phase_flow(
        SimpleNamespace(dry_run=False),
        state,
        None,
        hub,
        logging.getLogger("test"),
        ((Mock(return_value=True), (Phase.FINALIZATION,), Phase.FINALIZATION),),
        "switchover",
        lambda _state, message, _logger: failed.append(message) or False,
        Mock(),
        Mock(),
    )
    assert failed and failed[-1].startswith("Migration evidence refused (restore_not_completed:")
    assert "remove only the `completed_steps` entry named `wait_restore_completion`" in failed[-1]
    frozen = copy.deepcopy(RunRecord(state).migration_backups())

    def edited(remove_wait_marker):
        raw = json.loads(Path(state.state_file).read_text(encoding="utf-8"))
        raw["current_phase"] = Phase.ACTIVATION.value
        if remove_wait_marker:
            raw["completed_steps"] = [s for s in raw["completed_steps"] if s["name"] != "wait_restore_completion"]
        Path(state.state_file).write_text(json.dumps(raw), encoding="utf-8")
        return StateManager(state.state_file)

    phase_only = edited(remove_wait_marker=False)
    with _no_wait():
        assert _activation(hub, phase_only, "full_restore").activate() is False
    assert "no completion evidence" in phase_only.get_errors()[-1]["error"]

    recovered = edited(remove_wait_marker=True)
    with _no_wait():
        assert _activation(hub, recovered, "full_restore").activate() is True
    journal = RunRecord(recovered).migration_backups()
    assert journal["restore"]["completed_at"] is not None
    assert journal["backups"] == frozen["backups"] and journal["run_id"] == frozen["run_id"]
    assert [kind for kind, _, _ in hub.mutations] == ["create"]


def test_a_backup_drifting_during_the_passive_delete_blocks_the_create(state):
    hub = _hub(state)
    original = hub.delete_restore_guarded

    def delete_then_drift(name, **kwargs):
        result = original(name, **kwargs)
        hub.backups[MC_B]["metadata"]["uid"] = "uid-replacement"
        return result

    hub.delete_restore_guarded = delete_then_drift
    with _no_wait(), pytest.raises(FatalError, match="replaced or its status drifted"):
        _activation(hub, state, "passive_restore")._apply_migration_mutation()
    assert [kind for kind, _, _ in hub.mutations] == ["delete"]
    assert RunRecord(state).migration_backups()["restore"]["uid"] is None


# --- Task 7 review findings (Codex, 1193c5e0..06b346a3) ------------------------------------------


def test_f1_a_replacement_passive_restore_is_never_deleted(state):
    """Discovery sees U1; before the deletion read the name holds U2 (sync off, Started): no delete."""
    hub = _hub(state)
    original_list = hub.list_custom_resources_strict

    def list_then_replace(group, version, plural, namespace=None, label_selector=None):
        outcome = original_list(group, version, plural, namespace, label_selector)
        if plural == "restores" and group == CLUSTER_BACKUP_API_GROUP:
            hub.acm[RESTORE_PASSIVE_SYNC_NAME] = passive_restore(uid="uid-u2", sync=False, phase="Started")
        return outcome

    hub.list_custom_resources_strict = list_then_replace
    with _no_wait(), pytest.raises(FatalError, match="replaced"):
        _activation(hub, state, "passive_restore")._apply_migration_mutation()
    assert hub.mutations == []
    assert hub.acm[RESTORE_PASSIVE_SYNC_NAME]["metadata"]["uid"] == "uid-u2"


@pytest.mark.parametrize(
    "mutate",
    [
        pytest.param(lambda raw: raw["spec"].update(syncRestoreWithNewBackups=False), id="sync-off"),
        pytest.param(lambda raw: raw["status"].update(phase="Started"), id="not-ready"),
    ],
)
def test_f1_the_final_deletion_read_rechecks_eligibility_and_readiness(state, mutate):
    hub = _hub(state)
    original_list = hub.list_custom_resources_strict

    def list_then_change(group, version, plural, namespace=None, label_selector=None):
        outcome = original_list(group, version, plural, namespace, label_selector)
        if plural == "restores" and group == CLUSTER_BACKUP_API_GROUP:
            mutate(hub.acm[RESTORE_PASSIVE_SYNC_NAME])
        return outcome

    hub.list_custom_resources_strict = list_then_change
    with _no_wait(), pytest.raises(FatalError):
        _activation(hub, state, "passive_restore")._apply_migration_mutation()
    assert hub.mutations == []


class TestF2CompletedResumeRerunsTheContract:
    def _completed(self, state, kind="full_restore", version="2.14.3"):
        hub, activation = _mutated(state, kind, version)
        complete(hub, RESTORE_NAMES[kind], version=version, kind=kind)
        _wait(activation)
        return hub, RunRecord(state).migration_backups()

    def test_a_finished_with_errors_phase_blocks_completed_resume(self, state):
        hub, journal = self._completed(state)
        hub.acm[RESTORE_FULL_NAME]["status"]["phase"] = "FinishedWithErrors"
        with pytest.raises(FatalError, match="FinishedWithErrors"):
            _activation(hub, state, "full_restore")._reconcile_restore_completion()
        assert RunRecord(state).migration_backups() == journal

    def test_a_failed_owner_child_of_an_unfrozen_backup_blocks_completed_resume(self, state):
        hub, journal = self._completed(state)
        stray = child_name(RESTORE_FULL_NAME, "acm-resources-schedule-20240101000000")
        hub.velero[stray] = velero_child(
            stray, "acm-resources-schedule-20240101000000", RESTORE_FULL_NAME, f"uid-{RESTORE_FULL_NAME}", "Failed"
        )
        with pytest.raises(FatalError, match="velero_restore_not_completed|owner_child_unfrozen_backup"):
            _wait(_activation(hub, state, "full_restore"))
        assert RunRecord(state).migration_backups() == journal


def test_f3_completion_is_reproved_after_the_managed_cluster_wait(state):
    hub, activation = _mutated(state, "passive_patch")
    complete(hub, RESTORE_PASSIVE_SYNC_NAME, version="2.14.3", kind="passive_patch")
    original = activation._verify_managed_clusters_restored

    def verify_then_fail(**kwargs):
        hub.acm[RESTORE_PASSIVE_SYNC_NAME]["status"]["phase"] = "FinishedWithErrors"
        return original(**kwargs)

    activation._verify_managed_clusters_restored = verify_then_fail
    with pytest.raises(FatalError, match="FinishedWithErrors"):
        _wait(activation)
    journal = RunRecord(state).migration_backups()
    assert journal["restore"]["spec_fingerprint"] is None and journal["restore"]["completed_at"] is None


def test_f3_a_frozen_backup_drifting_during_the_wait_blocks_the_bundle(state):
    hub, activation = _mutated(state, "full_restore")
    complete(hub, RESTORE_FULL_NAME, version="2.14.3", kind="full_restore")
    original = activation._wait_for_managed_clusters_restored

    def wait_then_drift(timeout, **kwargs):
        hub.backups[CRED_B]["status"]["warnings"] = 3
        return original(timeout, **kwargs)

    activation._wait_for_managed_clusters_restored = wait_then_drift
    with pytest.raises(FatalError, match="drifted"):
        _wait(activation)
    assert RunRecord(state).migration_backups()["restore"]["spec_fingerprint"] is None


@pytest.mark.parametrize(
    "mutate",
    [
        pytest.param(lambda raw: raw["metadata"].update(uid="uid-u2"), id="replaced"),
        pytest.param(lambda raw: raw["status"].update(completionTimestamp="2024-01-02T13:00:00Z"), id="projection"),
    ],
)
@pytest.mark.parametrize("kind", KINDS)
def test_f4_accepted_mutation_resume_revalidates_frozen_backups(state, kind, mutate):
    hub, activation = _mutated(state, kind)
    journal = RunRecord(state).migration_backups()
    mutate(hub.backups[CRED_B])
    with pytest.raises(FatalError, match="replaced or its status drifted"):
        _activation(hub, state, kind)._reconcile_migration_mutation()
    with _no_wait(), pytest.raises(FatalError, match="replaced or its status drifted"):
        _activation(hub, state, kind)._apply_migration_mutation()
    assert RunRecord(state).migration_backups() == journal


def test_f4_completed_resume_revalidates_frozen_backups(state):
    hub, activation = _mutated(state, "passive_patch")
    complete(hub, RESTORE_PASSIVE_SYNC_NAME, version="2.14.3", kind="passive_patch")
    _wait(activation)
    journal = RunRecord(state).migration_backups()
    hub.backups[GEN_B]["metadata"]["uid"] = "uid-u2"
    with pytest.raises(FatalError, match="replaced or its status drifted"):
        _activation(hub, state, "passive_patch")._reconcile_restore_completion()
    assert RunRecord(state).migration_backups() == journal


def test_f6_an_accepted_create_without_generation_is_never_bound(state):
    hub = _hub(state, passive=False)
    original = hub.create_restore_guarded

    def create_without_generation(body, **kwargs):
        result = original(body, **kwargs)
        hub.acm[body["metadata"]["name"]]["metadata"]["generation"] = 2
        return dict(result, generation=None, generation_reported=False)

    hub.create_restore_guarded = create_without_generation
    with pytest.raises(FatalError, match="generation"):
        _activation(hub, state, "full_restore")._apply_migration_mutation()
    journal = RunRecord(state).migration_backups()
    assert journal["restore"]["uid"] is None and journal["restore"]["generation"] is None


@pytest.mark.parametrize(
    "result",
    [
        FakeHub._result(reason="unverifiable"),
        FakeHub._result(conflict=True, reason="already_exists"),
        FakeHub._result(http_accepted=True, reason="malformed_response"),
    ],
)
def test_f7_a_non_accepted_create_never_triggers_another_mutation(state, result):
    hub = _hub(state)
    hub.create_override = result
    hub.create_custom_resource = lambda *a, **k: pytest.fail("the passive sync Restore was recreated")
    with _no_wait(), pytest.raises(FatalError, match="not accepted"):
        _activation(hub, state, "passive_restore")._apply_migration_mutation()
    assert [kind for kind, _, _ in hub.mutations] == ["delete", "create"]
    assert RESTORE_PASSIVE_SYNC_NAME not in hub.acm
    journal = RunRecord(state).migration_backups()
    assert journal["restore"]["uid"] is None and journal["restore"]["generation"] is None


def test_f7_an_ambiguous_create_that_landed_is_refused_on_the_next_run(state):
    hub = _hub(state)
    original = hub.create_restore_guarded

    def create_then_time_out(body, **kwargs):
        original(body, **kwargs)
        return FakeHub._result(reason="unverifiable")

    hub.create_restore_guarded = create_then_time_out
    with _no_wait(), pytest.raises(FatalError, match="not accepted"):
        _activation(hub, state, "passive_restore")._apply_migration_mutation()
    hub.mutations.clear()
    with _no_wait(), pytest.raises(FatalError, match="never adopted"):
        _activation(hub, state, "passive_restore")._apply_migration_mutation()
    assert hub.mutations == []
