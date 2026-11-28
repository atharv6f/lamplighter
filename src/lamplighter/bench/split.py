"""The split of a task set into a dev set and a held-out set, worked out from a seed."""

import hashlib
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from lamplighter.ports import Failure

SOURCE = "split"
DEV_PLACES = 10


@dataclass(frozen=True, slots=True)
class Split:
    """The task names on each side, each in the order of the names."""

    dev: tuple[str, ...]
    held_out: tuple[str, ...]


def split(seed: str, groups: Mapping[str, Sequence[str]]) -> Split | Failure:
    """Give each group's share of the ten dev places, and take its tasks by hash.

    A group's share is in proportion to its size, rounded down, and the
    places still open go to the largest remainders, ties by the group's
    hash. Within a group the tasks are taken in the order of their hashes,
    so neither a name's place in the alphabet nor the order given decides.
    It is for a task set that is split once: adding a task can move old
    ones. A group that would have to give more tasks than it holds gives
    `invalid_input`.
    """
    total = sum(len(names) for names in groups.values())
    shares = {
        group: DEV_PLACES * len(names) // total for group, names in groups.items()
    }
    left = {group: DEV_PLACES * len(names) % total for group, names in groups.items()}
    still_open = DEV_PLACES - sum(shares.values())
    ranked = sorted(
        groups, key=lambda group: (-left[group], _hex(f"{seed}:group:{group}"))
    )
    for group in ranked[:still_open]:
        shares[group] += 1
    dev: list[str] = []
    held_out: list[str] = []
    for group, names in groups.items():
        if shares[group] > len(names):
            return Failure(
                kind="invalid_input",
                effect="none",
                message=f"The group {group} holds fewer tasks than its dev places.",
                source=SOURCE,
            )
        ordered = sorted(names, key=lambda name: _hex(f"{seed}:{name}"))
        dev.extend(ordered[: shares[group]])
        held_out.extend(ordered[shares[group] :])
    return Split(tuple(sorted(dev)), tuple(sorted(held_out)))


def _hex(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()
