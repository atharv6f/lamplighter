"""The words of `lamplighter models bakeoff`, and which command each one runs."""

import argparse
from pathlib import Path

from lamplighter.app.bakeoff import capture, choose, table, write_rule
from lamplighter.app.bakeoff_measure import MeasureParts, run
from lamplighter.app.bakeoff_soak import soak


def add_bakeoff_parts(bakeoff: argparse.ArgumentParser) -> None:
    """Add the bake-off's six commands and their arguments."""
    steps = bakeoff.add_subparsers(dest="step", required=True)
    built = steps.add_parser("capture", help="build the capture set")
    built.add_argument("--settings", type=Path, required=True, help="settings file")
    built.add_argument(
        "--count-with",
        type=Path,
        action="append",
        required=True,
        help="a settings file with one llama_cpp entry of a candidate; repeat it",
    )
    rule = steps.add_parser("rule", help="write the rule row")
    rule.add_argument("--replaces", default="", help="the rule hash it replaces")
    rule.add_argument("--why", default="", help="why it replaces it")
    measured = steps.add_parser("run", help="measure one candidate")
    _candidate(measured)
    measured.add_argument(
        "--notes", type=Path, required=True, help="what you wrote down for it"
    )
    _candidate(steps.add_parser("soak", help="soak one candidate for a day"))
    steps.add_parser("choose", help="apply the rule and write the choice row")
    steps.add_parser("table", help="print the table")


async def bakeoff_command(parts: MeasureParts, args: argparse.Namespace) -> int:
    """Run the bake-off command the arguments name."""
    bakeoff = parts.bakeoff
    if args.step == "capture":
        return await capture(bakeoff, args.settings, args.count_with)
    if args.step == "rule":
        return await write_rule(bakeoff, args.replaces, args.why)
    if args.step == "run":
        return await run(parts, args.settings, args.model, args.notes)
    if args.step == "soak":
        return await soak(parts, args.settings, args.model)
    if args.step == "choose":
        return await choose(bakeoff)
    return await table(bakeoff)


def _candidate(command: argparse.ArgumentParser) -> None:
    command.add_argument("--settings", type=Path, required=True, help="settings file")
    command.add_argument("--model", required=True, help="the candidate's entry")
