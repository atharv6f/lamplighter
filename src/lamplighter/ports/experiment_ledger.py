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
type BakeoffStage = Literal["rule", "candidate", "soak", "choice"]


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
class Verdict:
    """One requirement by name, whether it passed, and its evidence in a line."""

    name: str
    passed: bool
    evidence: str


@dataclass(frozen=True, slots=True)
class BakeoffBody:
    """One step of the bake-off of local model servers.

    A `rule` row holds the rule's values in `rule` and their hash in
    `rule_hash`, and, when it is not the first, the hash of the rule row
    it `replaces` and `why`. Every other row holds in `rule_hash` the
    hash of the rule row it was measured under, and leaves `rule` empty.

    A `candidate` row holds the hard requirements in `hard` and the
    measures in `criteria`. A `soak` row holds `stays_up` in `criteria`,
    as an object with at least `passed`, true or false. A `choice` row is
    `chosen` with the winner's server, model and weights, or not chosen
    with those empty, and says `why`.
    """

    stage: BakeoffStage
    rule: JsonObject
    rule_hash: str
    replaces: str
    why: str
    capture_set_hash: str
    capture_counts: JsonObject
    server: str
    server_version: str
    model: str
    weights_sha256: str
    weights_source: str
    quant: str
    hard: tuple[Verdict, ...]
    criteria: JsonObject
    chosen: bool
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


@dataclass(frozen=True, slots=True)
class BakeoffRow:
    """A step of the bake-off. It belongs to no experiment and names no config id."""

    kind: Literal["bakeoff"]
    row_id: str
    at: str
    experiment: str
    config_ids: tuple[str, ...]
    code_revision: str
    body: BakeoffBody
    v: int = 1


type LedgerRow = (
    PlanRow | StartRow | RunSetRow | DecisionRow | CorrectionRow | BakeoffRow
)


class ExperimentLedger(Protocol):
    """The book of experiments. Rows are only added, and the rules are checked first.

    `append` gives None when the row is in, the failure of
    `refused_row` when a rule refuses it, and `unavailable` when the
    book cannot be read, written or locked, with nothing added. A kill
    during `append` leaves the row whole or absent. Two appends at once
    are answered as if one had gone first. `rows` gives every row that
    `append` answered for, in the order they were added, and also a row
    whose `append` was killed after it was written whole.

    For the bake-off, `refused_row` refuses a second `rule` row that does
    not name the latest one as the one it `replaces` and say `why`; a
    `candidate` or `soak` row whose rule hash or capture set hash is not
    the latest rule row's; and a chosen `choice` row whose winner has no
    passing `soak` row under the same rule.
    """

    async def append(self, row: LedgerRow) -> Failure | None:
        """Add one row, if no rule refuses it."""
        ...

    async def rows(self) -> Sequence[LedgerRow] | Failure:
        """Give every row, in the order they were added."""
        ...
