from __future__ import annotations

import random
from typing import Callable, Protocol, runtime_checkable

from onuw.observations import (
    DrunkSwapped,
    InsomniacWoke,
    LoneWolfPeek,
    Observation,
    Robbed,
    SawWerewolves,
    SawWerewolvesAsMinion,
    SeerPeekedCenter,
    SeerPeekedPlayer,
    TroublemakerSwapped,
)
from onuw.state import (
    Action,
    DrunkSwapAction,
    LoneWolfPeekAction,
    NoAction,
    PrivateView,
    RobberStealAction,
    SeerPeekCenterAction,
    SeerPeekPlayerAction,
    TroublemakerSwapAction,
)


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


# ---------------------------------------------------------------------------
# Human-readable action / observation descriptions
# ---------------------------------------------------------------------------

def _describe_action(action: Action, player_count: int) -> str:
    n = player_count
    if isinstance(action, NoAction):
        return "No action."
    if isinstance(action, LoneWolfPeekAction):
        return f"Peek at center slot {action.center_position - n}."
    if isinstance(action, SeerPeekPlayerAction):
        return f"Peek at Player {action.target}'s card."
    if isinstance(action, SeerPeekCenterAction):
        c1, c2 = action.targets
        return f"Peek at center slots {c1 - n} and {c2 - n}."
    if isinstance(action, RobberStealAction):
        return f"Rob Player {action.target} (swap your card with theirs and learn your new card)."
    if isinstance(action, TroublemakerSwapAction):
        return f"Swap Player {action.target_a}'s and Player {action.target_b}'s cards."
    if isinstance(action, DrunkSwapAction):
        return f"Swap your card with center slot {action.center_position - n} (you won't see your new role)."
    return str(action)


def _describe_observation(obs: Observation, player_count: int) -> str:
    if isinstance(obs, SawWerewolves):
        if obs.wolf_positions:
            others = ", ".join(f"Player {p}" for p in obs.wolf_positions)
            return f"The other Werewolves are: {others}."
        return "You are the lone Werewolf — no other Werewolves are among the players."
    if isinstance(obs, SawWerewolvesAsMinion):
        if obs.wolf_positions:
            wolves = ", ".join(f"Player {p}" for p in obs.wolf_positions)
            return f"As the Minion you see that the Werewolves are: {wolves}."
        return "As the Minion you see there are no Werewolves among the players."
    if isinstance(obs, LoneWolfPeek):
        slot = obs.center_position - player_count
        return f"You peeked at center slot {slot}: it is {obs.role.value.upper()}."
    if isinstance(obs, SeerPeekedPlayer):
        return f"You peeked at Player {obs.target}: they hold {obs.role.value.upper()}."
    if isinstance(obs, SeerPeekedCenter):
        c1, c2 = obs.targets
        r1, r2 = obs.roles
        s1, s2 = c1 - player_count, c2 - player_count
        return f"You peeked at center slots {s1} ({r1.value.upper()}) and {s2} ({r2.value.upper()})."
    if isinstance(obs, Robbed):
        return (
            f"You robbed Player {obs.target}. "
            f"Your new role is {obs.new_role.value.upper()}."
        )
    if isinstance(obs, TroublemakerSwapped):
        return f"You swapped Player {obs.target_a} and Player {obs.target_b} (you don't know what they have)."
    if isinstance(obs, DrunkSwapped):
        slot = obs.center_position - player_count
        return f"You swapped your card with center slot {slot}. You don't know your new role."
    if isinstance(obs, InsomniacWoke):
        return f"After all night actions your card is: {obs.final_role.value.upper()}."
    return str(obs)


# ---------------------------------------------------------------------------
# Human agent — reads all decisions from stdin
# ---------------------------------------------------------------------------

class HumanAgent:
    """Interactive agent that prompts the human player via stdin/stdout."""

    def __init__(self, input_fn: Callable[[], str] = input) -> None:
        self._input = input_fn

    def _read_int(self, prompt: str, lo: int, hi: int) -> int:
        """Loop until the user enters an integer in [lo, hi]."""
        while True:
            raw = self._input(prompt).strip()
            try:
                val = int(raw)
                if lo <= val <= hi:
                    return val
            except ValueError:
                pass
            print(f"  Please enter a number between {lo} and {hi}.")

    def night_action(self, view: PrivateView, legal_actions: list[Action]) -> Action:
        if len(legal_actions) == 1:
            return legal_actions[0]
        print("\nChoose your night action:")
        for i, action in enumerate(legal_actions, 1):
            print(f"  {i}. {_describe_action(action, view.player_count)}")
        idx = self._read_int("Your choice: ", 1, len(legal_actions)) - 1
        return legal_actions[idx]

    def speak(self, view: PrivateView, public_log: list[str]) -> str:
        raw = self._input("Your statement: ").strip()
        return raw if raw else "Pass."

    def vote(self, view: PrivateView, public_log: list[str]) -> int:
        targets = view.legal_vote_targets()
        print(f"\nVote to eliminate. Valid targets: {', '.join(str(t) for t in targets)}")
        while True:
            raw = self._input("Your vote (seat number): ").strip()
            try:
                target = int(raw)
                if target in targets:
                    return target
            except ValueError:
                pass
            print(f"  Please enter one of: {targets}")

    def show_night_result(self, view: PrivateView) -> None:
        """Display the human's private observations after the night phase."""
        print(f"\nYour dealt role: {view.dealt_role.value.upper()}")
        if view.observations:
            print("Night observations:")
            for obs in view.observations:
                print(f"  - {_describe_observation(obs, view.player_count)}")
        else:
            print("Night observations: (none)")
