"""Builds each model client the contract tests run against.

A test writes the turns it wants as scripted turns. Each maker here turns
that script into a client of one kind that will play it.
"""

import random
from collections.abc import Callable, Sequence

from lamplighter.adapters import ScriptedBreak, ScriptedModel, ScriptedReply
from lamplighter.ports import ModelClient
from tests.model_samples import PROFILE

type Turn = ScriptedReply | ScriptedBreak
type ClientMaker = Callable[[Sequence[Turn]], ModelClient]


def scripted(turns: Sequence[Turn]) -> ModelClient:
    """Give the scripted model itself, with cuts that differ on every run."""
    return ScriptedModel(PROFILE, turns, random.Random())


MAKERS: dict[str, ClientMaker] = {"scripted": scripted}
