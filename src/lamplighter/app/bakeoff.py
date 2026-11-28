"""The bake-off's commands that read and write the ledger: capture, rule, choose, table.

The two that ask a running server, `run` and `soak`, are in
`bakeoff_measure.py`. Every command here reads only the ledger, the
bench folder and the checkout, and a server only to count.
"""

import dataclasses
import tomllib
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path

from lamplighter.app.bench_parts import BenchParts, ledger_rows, task_view
from lamplighter.app.capture_set import (
    CapturedRun,
    Rebuild,
    capture_line,
    chosen,
    first_pass,
    request_hash,
    set_hash,
)
from lamplighter.app.capture_sources import (
    Gathered,
    TrialSettings,
    chat_runs,
    trial_runs,
)
from lamplighter.app.config import Config, load_config
from lamplighter.app.model_set import ModelSet
from lamplighter.bench import (
    BakeoffChoice,
    BakeoffRule,
    Candidate,
    bakeoff_choice,
    bakeoff_rule_hash,
    candidate_of,
    read_bakeoff_rule,
)
from lamplighter.ports import (
    BakeoffBody,
    BakeoffRow,
    BakeoffStage,
    EventLog,
    Failure,
    JsonObject,
    ModelClient,
    read_saved_json,
    saved_json,
    to_plain,
    utc_text,
)

RULE_FILE = Path("bench") / "bakeoff_rule.toml"
CAPTURES = "captures"


@dataclass(frozen=True)
class BakeoffParts:
    """What the bake-off's commands are built from. A test gives its own.

    `models` builds the clients of a settings file. `chat_ids` gives the
    config ids, private and not, that a chat on a settings file saves.
    `rebuild` gives what a request takes from a settings file. `open_log`
    opens the store a settings file names, and gives its log and how to
    close it.
    """

    bench: BenchParts
    models: Callable[[Config], ModelSet]
    chat_ids: Callable[[Config], frozenset[str]]
    rebuild: Callable[[Config], Rebuild]
    open_log: Callable[[Config], tuple[EventLog, Callable[[], None]] | Failure]

    def refuse(self, message: str) -> int:
        """Print why the command stops, and give its exit code."""
        return self.bench.refuse(message)

    def say(self, line: str) -> None:
        """Print one line of the command's answer."""
        print(line, file=self.bench.out)

    def captures(self) -> Path:
        """Give the folder the capture sets are written to, in the bench folder."""
        return Path(self.bench.settings.dir) / CAPTURES

    def rule(self) -> BakeoffRule | Failure:
        """Read the bake-off's rule from the checkout."""
        path = self.bench.root / RULE_FILE
        try:
            plain = tomllib.loads(path.read_text(encoding="utf-8"))
        except (OSError, tomllib.TOMLDecodeError) as error:
            return _refused(f"{path} could not be read: {error}.")
        return read_bakeoff_rule(plain)


async def capture(
    parts: BakeoffParts, settings: Path, count_with: Sequence[Path]
) -> int:
    """Build the capture set from the store and the bench, write it, say what it has.

    It refuses to write into a folder inside a git checkout, because the set
    holds your own messages.
    """
    folder = parts.captures()
    if _in_checkout(folder):
        return parts.refuse(f"{folder} is inside a git checkout.")
    config = _loaded(settings)
    if isinstance(config, str):
        return parts.refuse(config)
    built = await _capture_set(parts, config, count_with)
    if isinstance(built, Failure):
        return parts.refuse(built.message)
    text, summary = built
    written = _written(folder, set_hash(text), text, summary)
    if isinstance(written, Failure):
        return parts.refuse(written.message)
    for key, value in summary.items():
        parts.say(f"{key}: {saved_json(value)}")
    parts.say(f"Written to {written}.")
    return 0


async def _capture_set(
    parts: BakeoffParts, config: Config, count_with: Sequence[Path]
) -> tuple[str, JsonObject] | Failure:
    rule = parts.rule()
    if isinstance(rule, Failure):
        return rule
    gathered = await _gathered(parts, config)
    if isinstance(gathered, Failure):
        return gathered
    chat, trials = gathered
    held_out = _held_out_texts(parts)
    if isinstance(held_out, Failure):
        return held_out
    passed = first_pass((*chat.runs, *trials.runs), held_out)
    counts = await _counted(parts, passed.runs, count_with)
    if isinstance(counts, Failure):
        return counts
    found = chosen(passed.runs, counts, rule.capture_window, rule.capture_max_tokens)
    text = "".join(capture_line(c) + "\n" for c in found.requests)
    sizes = sorted(max(counts[request_hash(c.request)]) for c in found.requests)
    summary: JsonObject = {
        "capture_set_hash": set_hash(text),
        "requests": len(found.requests),
        "chat": sum(c.source == "chat" for c in found.requests),
        "trials": sum(c.source == "trial" for c in found.requests),
        "runs_passed_over": chat.passed_over + trials.passed_over,
        "held_out": passed.held_out,
        "repeats": passed.repeats,
        "over_ten_a_run": passed.over_the_cap,
        "too_large": found.too_large,
        "sizes": [sizes[0], sizes[len(sizes) // 2], sizes[-1]] if sizes else [],
    }
    return text, summary


async def write_rule(parts: BakeoffParts, replaces: str, why: str) -> int:
    """Write the rule row, with the latest capture set's hash and counts."""
    rule = parts.rule()
    if isinstance(rule, Failure):
        return parts.refuse(rule.message)
    summary = latest_capture(parts.captures())
    if isinstance(summary, Failure):
        return parts.refuse(summary.message)
    digest = summary["capture_set_hash"]
    body = empty_body("rule", bakeoff_rule_hash(rule))
    body = dataclasses.replace(
        body,
        rule=to_plain(rule),
        replaces=replaces,
        why=why,
        capture_set_hash=digest if isinstance(digest, str) else "",
        capture_counts=summary,
    )
    answer = await append_step(parts, parts.bench.ids.new_id(), body)
    if answer is not None:
        return parts.refuse(answer.message)
    parts.say(f"The rule row is in: {body.rule_hash}, on the capture set {digest}.")
    return 0


async def choose(parts: BakeoffParts) -> int:
    """Apply the rule to the rows and write the choice row, unless a soak is due."""
    rows = await ledger_rows(parts.bench)
    if isinstance(rows, Failure):
        return parts.refuse(rows.message)
    choice = bakeoff_choice(rows)
    if isinstance(choice, Failure):
        return parts.refuse(choice.message)
    if choice.to_soak is not None:
        return parts.refuse(f"Soak {_named(choice.to_soak)} first: {choice.why}.")
    body = dataclasses.replace(
        empty_body("choice", choice.rule_hash),
        capture_set_hash=choice.capture_set_hash,
    )
    winner = choice.chosen
    if winner is not None:
        body = dataclasses.replace(
            body,
            server=winner.server,
            model=winner.model,
            weights_sha256=winner.weights_sha256,
            chosen=True,
        )
    body = dataclasses.replace(body, why=choice.why)
    answer = await append_step(parts, parts.bench.ids.new_id(), body)
    if answer is not None:
        return parts.refuse(answer.message)
    parts.say(f"Chosen: {'none' if winner is None else _named(winner)}, {choice.why}.")
    return 0


async def table(parts: BakeoffParts) -> int:
    """Print each candidate of the latest rule, its measures, its soak, the choice."""
    rows = await ledger_rows(parts.bench)
    if isinstance(rows, Failure):
        return parts.refuse(rows.message)
    choice = bakeoff_choice(rows)
    if isinstance(choice, Failure):
        return parts.refuse(choice.message)
    steps = [r.body for r in rows if isinstance(r, BakeoffRow)]
    hashes = (choice.rule_hash, choice.capture_set_hash)
    under = [s for s in steps if (s.rule_hash, s.capture_set_hash) == hashes]
    for line in table_lines(under, choice):
        parts.say(line)
    return 0


def table_lines(under: Sequence[BakeoffBody], choice: BakeoffChoice) -> list[str]:
    """Give a line for each candidate under the rule, in its order, then the choice."""
    latest = {candidate_of(s): s for s in under if s.stage == "candidate"}
    soaks = {candidate_of(s): s for s in under if s.stage == "soak"}
    out = {left.candidate: left.why for left in choice.out}
    lines: list[str] = []
    for candidate in (*choice.order, *out):
        body = latest[candidate]
        hard = ", ".join(
            f"{v.name} {'pass' if v.passed else 'fail'}" for v in body.hard
        )
        soak = soaks.get(candidate)
        lines.append(
            f"{_named(candidate)}  {body.server_version}  {body.quant}  hard: {hard}"
            f"  criteria: {saved_json(body.criteria)}"
            f"  soak: {'none' if soak is None else saved_json(soak.criteria)}"
            f"  {out.get(candidate, 'in')}"
        )
    choices = [s for s in under if s.stage == "choice"]
    if choices:
        last = choices[-1]
        named = f"{last.server} {last.model}" if last.chosen else "none"
        lines.append(f"Chosen: {named}, because {last.why}.")
    elif choice.to_soak is not None:
        lines.append(f"Not chosen yet: {choice.why}.")
    return lines


def empty_body(stage: BakeoffStage, rule_hash: str) -> BakeoffBody:
    """Give a bake-off body of the stage under the rule, every other field empty."""
    return BakeoffBody(
        stage=stage,
        rule={},
        rule_hash=rule_hash,
        replaces="",
        why="",
        capture_set_hash="",
        capture_counts={},
        server="",
        server_version="",
        model="",
        weights_sha256="",
        weights_source="",
        quant="",
        hard=(),
        criteria={},
        chosen=False,
        note="",
    )


async def append_step(
    parts: BakeoffParts, row_id: str, body: BakeoffBody
) -> Failure | None:
    """Add one bake-off row to the ledger, now, with the id given."""
    bench = parts.bench
    row = BakeoffRow(
        kind="bakeoff",
        row_id=row_id,
        at=utc_text(bench.clock.now()),
        experiment="",
        config_ids=(),
        code_revision=bench.revision(),
        body=body,
    )
    return await bench.ledger.append(row)


def latest_capture(folder: Path) -> JsonObject | Failure:
    """Give the summary of the capture set written last."""
    summaries = sorted(folder.glob("capture-*.json"), key=lambda p: p.stat().st_mtime)
    if not summaries:
        return _refused(f"There is no capture set in {folder}. Run the capture first.")
    try:
        plain = read_saved_json(summaries[-1].read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        return _refused(f"{summaries[-1]} could not be read: {error}.")
    if not isinstance(plain, dict):
        return _refused(f"{summaries[-1]} is not a capture summary.")
    return plain


def capture_path(folder: Path, digest: str) -> Path:
    """Give where the capture set with this hash is written."""
    return folder / f"capture-{digest[:12]}.jsonl"


async def _gathered(
    parts: BakeoffParts, config: Config
) -> tuple[Gathered, Gathered] | Failure:
    opened = parts.open_log(config)
    if isinstance(opened, Failure):
        return opened
    log, close = opened
    try:
        chat = await chat_runs(log, parts.chat_ids(config), parts.rebuild(config))
    finally:
        close()
    if isinstance(chat, Failure):
        return chat
    rows = await ledger_rows(parts.bench)
    if isinstance(rows, Failure):
        return rows
    run_sets = parts.bench.run_sets()
    trials = await trial_runs(rows, parts.bench.jobs, run_sets, _copy_of(parts))
    return trials if isinstance(trials, Failure) else (chat, trials)


def _copy_of(parts: BakeoffParts) -> Callable[[Path], TrialSettings | str]:
    def settings(path: Path) -> TrialSettings | str:
        config = _loaded(path)
        if isinstance(config, str):
            return config
        return TrialSettings(parts.bench.portable_id(config), parts.rebuild(config))

    return settings


def _held_out_texts(parts: BakeoffParts) -> list[str] | Failure:
    root = parts.bench.root
    view = task_view(root)
    if isinstance(view, Failure):
        return view
    if view.split is None:
        return _refused("The task set's split cannot be read.")
    texts: list[str] = []
    for task in view.split.held_out:
        path = root / "bench" / "tasks" / task / "instruction.md"
        try:
            texts.append(path.read_text(encoding="utf-8"))
        except OSError as error:
            return _refused(f"{path} could not be read: {error.strerror}.")
    return texts


async def _counted(
    parts: BakeoffParts, runs: Sequence[CapturedRun], count_with: Sequence[Path]
) -> dict[str, list[int]] | Failure:
    if not count_with:
        return _refused("Name at least one settings file to count with.")
    counts: dict[str, list[int]] = {
        request_hash(c.request): [] for run in runs for c in run.requests
    }
    for path in count_with:
        added = await _count_with(parts, runs, path, counts)
        if added is not None:
            return added
    return counts


async def _count_with(
    parts: BakeoffParts,
    runs: Sequence[CapturedRun],
    path: Path,
    counts: dict[str, list[int]],
) -> Failure | None:
    config = _loaded(path)
    if isinstance(config, str):
        return _refused(config)
    if len(config.models) != 1 or config.models[0].local is None:
        return _refused(f"{path} must hold exactly one model entry on this Mac.")
    if config.models[0].local.count != "llama_cpp":
        return _refused(f"{path}: its entry must count with llama_cpp.")
    models = parts.models(config)
    try:
        name = config.models[0].name
        down = await models.health[name].health()
        if down is not None:
            return _refused(f"The server of {path} is not up: {down.message}")
        return await _count_each(models.clients[name], runs, counts)
    finally:
        await models.close()


async def _count_each(
    client: ModelClient, runs: Sequence[CapturedRun], counts: dict[str, list[int]]
) -> Failure | None:
    for run in runs:
        for captured in run.requests:
            found = await client.count_tokens(captured.request)
            if isinstance(found, Failure):
                return found
            counts[request_hash(captured.request)].append(found)
    return None


def _written(
    folder: Path, digest: str, text: str, summary: JsonObject
) -> Path | Failure:
    path = capture_path(folder, digest)
    try:
        folder.mkdir(mode=0o700, parents=True, exist_ok=True)
        for target, content in (
            (path, text),
            (path.with_suffix(".json"), saved_json(summary) + "\n"),
        ):
            temporary = target.with_name(target.name + ".new")
            # Made private before the first byte is written, not after.
            temporary.touch(mode=0o600)
            temporary.chmod(0o600)
            temporary.write_text(content, encoding="utf-8")
            temporary.replace(target)
    except OSError as error:
        return _refused(f"The capture set could not be written: {error.strerror}.")
    return path


def _in_checkout(folder: Path) -> bool:
    resolved = folder.expanduser().resolve()
    return any((place / ".git").exists() for place in (resolved, *resolved.parents))


def _loaded(path: Path) -> Config | str:
    try:
        return load_config(path)
    except (OSError, ValueError) as error:
        return f"{path}: {error}"


def _named(candidate: Candidate) -> str:
    return f"{candidate.server} {candidate.model}"


def _refused(message: str) -> Failure:
    return Failure(
        kind="invalid_input", effect="none", message=message, source="bakeoff"
    )
