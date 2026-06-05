from __future__ import annotations

import random
from typing import Protocol, runtime_checkable

from onuw.state import Action, PrivateView


@runtime_checkable
class Agent(Protocol):
    def night_action(self, view: PrivateView, legal_actions: list[Action]) -> Action:
        ...

    def speak(self, view: PrivateView, public_log: list[str]) -> str:
        ...

    def vote(self, view: PrivateView, public_log: list[str]) -> int:
        ...


class RandomAgent:
    """Picks uniformly at random among legal actions and vote targets."""

    def __init__(self, rng: random.Random) -> None:
        self._rng = rng

    def night_action(self, view: PrivateView, legal_actions: list[Action]) -> Action:
        return self._rng.choice(legal_actions)

    def speak(self, view: PrivateView, public_log: list[str]) -> str:
        return "I have nothing to add."

    def vote(self, view: PrivateView, public_log: list[str]) -> int:
        candidates = view.legal_vote_targets()
        return self._rng.choice(candidates)


class ScriptedAgent:
    """Replays a fixed sequence of actions and always votes for the same target."""

    def __init__(
        self,
        actions: list[Action],
        vote_target: int,
        statements: list[str] | None = None,
    ) -> None:
        self._actions = list(actions)
        self._cursor = 0
        self._vote_target = vote_target
        self._statements = list(statements) if statements is not None else []
        self._statement_cursor = 0

    def night_action(self, view: PrivateView, legal_actions: list[Action]) -> Action:
        if self._cursor >= len(self._actions):
            raise IndexError(
                f"ScriptedAgent at seat {view.seat} has no more scripted actions "
                f"(cursor={self._cursor}, actions={self._actions!r})"
            )
        action = self._actions[self._cursor]
        self._cursor += 1
        return action

    def speak(self, view: PrivateView, public_log: list[str]) -> str:
        if self._statement_cursor >= len(self._statements):
            return "Pass."
        statement = self._statements[self._statement_cursor]
        self._statement_cursor += 1
        return statement

    def vote(self, view: PrivateView, public_log: list[str]) -> int:
        return self._vote_target
