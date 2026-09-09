"""Outcome algebra for decommission substeps.

This module is the sole Python definition of the decommission outcome
vocabulary. The Ansible collection mirrors it as a plain tuple and a parity
test compares the two; nothing else in Python may redefine these names.

Three values make up the contract:

* ``SubstepOutcome`` -- the five states one substep can end in, replacing the
  single ``True`` the old aggregator returned for all of them.
* ``SubstepExecution`` -- the ONE execution-result channel. Every substep
  executor, in every PR of this slice, returns exactly this shape. An expected
  operational failure is a returned ``FAILED`` execution, never an exception, so
  the mutation the failing substep actually performed reaches the aggregator.
* ``DecommissionResult`` -- what callers see. It deliberately defines no
  ``__bool__``: callers must test ``.succeeded`` explicitly, because a partially
  mutated run is both ``changed`` and unsuccessful and truthiness cannot say so.
"""

from dataclasses import dataclass, field
from enum import Enum
from typing import Optional


class SubstepOutcome(Enum):
    """How one decommission substep ended in this invocation."""

    NOT_REQUESTED = "not_requested"
    PRECONDITION_NOOP = "precondition_noop"
    COMPLETED = "completed"
    REFUSED = "refused"
    FAILED = "failed"


#: Outcomes that make a run unsuccessful. A refusal is as fatal as a failure:
#: the operator declined a required destructive step, so the run cannot claim
#: the hub was torn down.
UNSUCCESSFUL_OUTCOMES = (SubstepOutcome.REFUSED, SubstepOutcome.FAILED)


@dataclass(frozen=True)
class SubstepExecution:
    """The result of executing one substep in this invocation.

    ``changed`` is accepted mutation performed during THIS invocation -- never
    requested work, predicted work, a resumed obligation, a precondition noop,
    check mode, or dry run.

    At the B stage ``changed`` means the delete call was accepted by the client,
    which is as exact as the current primitive allows: ``delete_custom_resource``
    is declared ``@api_call(not_found_value=True)``, so it returns ``True`` both
    for a delete the API performed and for a 404 on an already-absent object,
    and the two are indistinguishable at the call site. A resource someone else
    removed concurrently can therefore report ``changed=True``. PR C's
    UID-preconditioned guarded delete reports the precise outcome and closes
    this; adding that primitive is out of scope for PR B.
    """

    outcome: SubstepOutcome
    changed: bool = False


class ObservabilityGateDecision(Enum):
    """What the destination-observability gate authorizes for THIS invocation."""

    #: The destination is positively present, or its proven absence was acknowledged.
    PROCEED = "proceed"
    #: The source is positively absent: there is nothing to delete, so nothing to gate.
    NOT_APPLICABLE = "not_applicable"
    BLOCKED = "blocked"


@dataclass(frozen=True)
class ObservabilityGateResult:
    """One gate evaluation. Never persisted, never cached, never resumed.

    ``reason`` is a stable reason code mirrored into the collection, not a
    message: it is the only part of the gate that reaches a log or an operator
    contract, so it can carry no cluster response text.
    """

    decision: ObservabilityGateDecision
    reason: Optional[str] = None

    def __post_init__(self) -> None:
        # A block with no reason is indistinguishable from a bug that blocked
        # everything, and the two destination reasons must never collapse.
        if self.decision is ObservabilityGateDecision.BLOCKED and not self.reason:
            raise ValueError("a blocked gate result must carry a reason code")
        if self.decision is not ObservabilityGateDecision.BLOCKED and self.reason is not None:
            raise ValueError(f"{self.decision.value} outcome must not carry a reason code")


@dataclass(frozen=True)
class DecommissionResult:
    """What ``Decommission.decommission`` reports to its callers."""

    substeps: dict[str, SubstepOutcome] = field(default_factory=dict)
    not_attempted: tuple[str, ...] = ()
    cancelled: bool = False  # top-level banner cancellation only
    changed: bool = False  # actual live mutation, never prediction
    would_change: bool = False  # fresh read-only prediction, never authority

    @property
    def succeeded(self) -> bool:
        """True only when nothing was cancelled, refused, or failed."""
        if self.cancelled:
            return False
        return not any(outcome in UNSUCCESSFUL_OUTCOMES for outcome in self.substeps.values())

    def summary_lines(self) -> list[str]:
        """Human-readable summary naming each substep's fate separately.

        Actual change and predicted change are labelled apart so a dry-run
        preview can never read as a completed teardown.
        """
        lines = []
        if self.cancelled:
            lines.append("decommission cancelled before any substep ran")
        for substep, outcome in self.substeps.items():
            lines.append(f"{substep}: {outcome.value}")
        for substep in self.not_attempted:
            lines.append(f"{substep}: not attempted")
        lines.append(f"actual change: {self.changed}")
        lines.append(f"predicted change: {self.would_change}")
        return lines
