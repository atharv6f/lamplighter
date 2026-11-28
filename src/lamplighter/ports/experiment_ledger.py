"""The port for the book of experiments: plans, run sets and decisions, only added."""

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Literal, Protocol

from lamplighter.ports.failure import Failure
from lamplighter.ports.json_value import JsonObject

type SplitName = Literal["dev", "held_out"]
type Side = Literal["baseline", "candidate"]
# A loose run set has no side.
type RunSetSide = Literal["baseline", "candidate", ""]


@dataclass(frozen=True, slots=True)
class SettingsFile:
    """A settings file a plan names, and the SHA-256 of its bytes."""

    path: str
    sha256: str


@dataclass(frozen=True, slots=True)
class PlanBody:
    """What an experiment was planned with, before any of its trials ran.

    `rule` is the gate rule's values, `rule_hash` their hash.
    """

    rule: JsonObject
    rule_hash: str
    split: SplitName
    task_set_hash: str
    tasks: tuple[str, ...]
    trials_per_task: int
    baseline: SettingsFile
    candidate: SettingsFile
    harbor_version: str
    note: str


@dataclass(frozen=True, slots=True)
class StartBody:
    """That one side of an experiment is about to run its first trial."""

    side: Side
    run_set: str
    split: SplitName


@dataclass(frozen=True, slots=True)
class RunSetBody:
    """A whole run set.

    `run_set` is the run set's own record as plain values, with at least
    its name, split, task set hash and trials per task. `side` is empty
    for a loose run set. The two times are the job runner's.
    """

    run_set: JsonObject
    side: RunSetSide
    totals: JsonObject
    started_at: str
    ended_at: str
    concurrent_trials: int
    note: str


@dataclass(frozen=True, slots=True)
class DecisionBody:
    """What the gate decided, as plain values with at least its rule hash.

    `held_out_uses` is the ledger's count of experiments started on the
    held-out set, with this row in.
    """

    decision: JsonObject
    held_out_uses: int
    same_config: bool


@dataclass(frozen=True, slots=True)
class CorrectionBody:
    """A note about an earlier row. It changes nothing any rule reads."""

    corrects: str
    note: str


@dataclass(frozen=True, slots=True)
class PlanRow:
    """An experiment's plan. `config_ids` are the baseline's and the candidate's."""

    kind: Literal["plan"]
    row_id: str
    at: str
    experiment: str
    config_ids: tuple[str, ...]
    code_revision: str
    body: PlanBody
    v: int = 1


@dataclass(frozen=True, slots=True)
class StartRow:
    """A side's start. `config_ids` is that side's one id."""

    kind: Literal["start"]
    row_id: str
    at: str
    experiment: str
    config_ids: tuple[str, ...]
    code_revision: str
    body: StartBody
    v: int = 1


@dataclass(frozen=True, slots=True)
class RunSetRow:
    """A run set. `experiment` is empty for a loose one.

    `config_ids` is its one id, or none for a baseline harness.
    """

    kind: Literal["run_set"]
    row_id: str
    at: str
    experiment: str
    config_ids: tuple[str, ...]
    code_revision: str
    body: RunSetBody
    v: int = 1


@dataclass(frozen=True, slots=True)
class DecisionRow:
    """A decision. `config_ids` are the baseline's and the candidate's."""

    kind: Literal["decision"]
    row_id: str
    at: str
    experiment: str
    config_ids: tuple[str, ...]
    code_revision: str
    body: DecisionBody
    v: int = 1


@dataclass(frozen=True, slots=True)
class CorrectionRow:
    """A correction. `experiment` is that of the row it corrects."""

    kind: Literal["correction"]
    row_id: str
    at: str
    experiment: str
    config_ids: tuple[str, ...]
    code_revision: str
    body: CorrectionBody
    v: int = 1


type LedgerRow = PlanRow | StartRow | RunSetRow | DecisionRow | CorrectionRow


class ExperimentLedger(Protocol):
    """The book of experiments. Rows are only added, and the rules are checked first.

    `append` gives None when the row is in, the failure of
    `refused_row` when a rule refuses it, and `unavailable` when the
    book cannot be read, written or locked, with nothing added. A kill
    during `append` leaves the row whole or absent. Two appends at once
    are answered as if one had gone first. `rows` gives every row that
    `append` answered for, in the order they were added, and also a row
    whose `append` was killed after it was written whole.
    """

    async def append(self, row: LedgerRow) -> Failure | None:
        """Add one row, if no rule refuses it."""
        ...

    async def rows(self) -> Sequence[LedgerRow] | Failure:
        """Give every row, in the order they were added."""
        ...
