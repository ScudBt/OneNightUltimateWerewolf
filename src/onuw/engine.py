from __future__ import annotations

import random
from itertools import combinations
from typing import TYPE_CHECKING

from onuw.observations import (
    DrunkSwapped,
    InsomniacWoke,
    LoneWolfPeek,
    Robbed,
    SawWerewolves,
    SawWerewolvesAsMinion,
    SeerPeekedCenter,
    SeerPeekedPlayer,
    TroublemakerSwapped,
)
from onuw.roles import Role
from onuw.state import (
    Action,
    DrunkSwapAction,
    GameConfig,
    GameResult,
    GameState,
    LoneWolfPeekAction,
    NoAction,
    Outcome,
    PrivateView,
    RobberStealAction,
    SeerPeekCenterAction,
    SeerPeekPlayerAction,
    TroublemakerSwapAction,
)

if TYPE_CHECKING:
    from onuw.agents import Agent


# ---------------------------------------------------------------------------
# Setup
# ---------------------------------------------------------------------------

def deal(config: GameConfig, rng: random.Random) -> GameState:
    positions = list(range(config.player_count + 3))
    role_list = list(config.roles)
    rng.shuffle(role_list)

    dealt: dict[int, Role] = {pos: role for pos, role in zip(positions, role_list)}
    current: dict[int, Role] = dict(dealt)

    state = GameState(
        player_count=config.player_count,
        dealt_roles=dealt,
        current_roles=current,
        public_log=[],
    )
    return state


# ---------------------------------------------------------------------------
# Legal-action computation
# ---------------------------------------------------------------------------

def get_legal_actions(state: GameState, seat: int) -> list[Action]:
    role = state.dealt_roles[seat]
    players = state.player_positions()
    centers = state.center_positions()

    if role == Role.WEREWOLF:
        wolf_seats = [p for p in players if state.dealt_roles[p] == Role.WEREWOLF]
        if len(wolf_seats) == 1:
            # Lone wolf: must choose a center card to peek
            return [LoneWolfPeekAction(c) for c in centers]
        else:
            return [NoAction()]

    if role == Role.SEER:
        player_peeks: list[Action] = [
            SeerPeekPlayerAction(p) for p in players if p != seat
        ]
        center_peeks: list[Action] = [
            SeerPeekCenterAction((c1, c2))
            for c1, c2 in combinations(centers, 2)
        ]
        return player_peeks + center_peeks

    if role == Role.ROBBER:
        return [RobberStealAction(p) for p in players if p != seat]

    if role == Role.TROUBLEMAKER:
        others = [p for p in players if p != seat]
        return [
            TroublemakerSwapAction(a, b)
            for a, b in combinations(others, 2)
        ]

    if role == Role.DRUNK:
        return [DrunkSwapAction(c) for c in centers]

    # MINION, INSOMNIAC, VILLAGER — no choice
    return [NoAction()]


# ---------------------------------------------------------------------------
# Night-action application (mutates state, records observations)
# ---------------------------------------------------------------------------

def apply_night_action(state: GameState, seat: int, action: Action) -> None:
    role = state.dealt_roles[seat]

    if role == Role.WEREWOLF:
        players = state.player_positions()
        wolf_seats = [p for p in players if state.dealt_roles[p] == Role.WEREWOLF]
        others = [w for w in wolf_seats if w != seat]
        state.add_observation(seat, SawWerewolves(tuple(others)))

        if len(wolf_seats) == 1:
            if not isinstance(action, LoneWolfPeekAction):
                raise ValueError(f"Lone wolf at seat {seat} must use LoneWolfPeekAction, got {action!r}")
            cpos = action.center_position
            state.add_observation(seat, LoneWolfPeek(cpos, state.current_roles[cpos]))
        else:
            if not isinstance(action, NoAction):
                raise ValueError(f"Multi-wolf at seat {seat} must use NoAction, got {action!r}")

    elif role == Role.SEER:
        if isinstance(action, SeerPeekPlayerAction):
            state.add_observation(
                seat, SeerPeekedPlayer(action.target, state.current_roles[action.target])
            )
        elif isinstance(action, SeerPeekCenterAction):
            c1, c2 = action.targets
            state.add_observation(
                seat,
                SeerPeekedCenter(
                    (c1, c2),
                    (state.current_roles[c1], state.current_roles[c2]),
                ),
            )
        else:
            raise ValueError(f"Seer at seat {seat} received invalid action {action!r}")

    elif role == Role.ROBBER:
        if not isinstance(action, RobberStealAction):
            raise ValueError(f"Robber at seat {seat} must use RobberStealAction, got {action!r}")
        target = action.target
        new_role = state.current_roles[target]
        state.current_roles[target] = state.current_roles[seat]
        state.current_roles[seat] = new_role
        state.add_observation(seat, Robbed(target, new_role))

    elif role == Role.TROUBLEMAKER:
        if not isinstance(action, TroublemakerSwapAction):
            raise ValueError(f"Troublemaker at seat {seat} must use TroublemakerSwapAction, got {action!r}")
        a, b = action.target_a, action.target_b
        state.current_roles[a], state.current_roles[b] = (
            state.current_roles[b],
            state.current_roles[a],
        )
        state.add_observation(seat, TroublemakerSwapped(a, b))

    elif role == Role.MINION:
        if not isinstance(action, NoAction):
            raise ValueError(f"Minion at seat {seat} must use NoAction, got {action!r}")
        players = state.player_positions()
        wolf_seats = [p for p in players if state.dealt_roles[p] == Role.WEREWOLF]
        state.add_observation(seat, SawWerewolvesAsMinion(tuple(wolf_seats)))

    elif role == Role.DRUNK:
        if not isinstance(action, DrunkSwapAction):
            raise ValueError(f"Drunk at seat {seat} must use DrunkSwapAction, got {action!r}")
        center_pos = action.center_position
        state.current_roles[seat], state.current_roles[center_pos] = (
            state.current_roles[center_pos],
            state.current_roles[seat],
        )
        state.add_observation(seat, DrunkSwapped(center_pos))

    elif role == Role.INSOMNIAC:
        if not isinstance(action, NoAction):
            raise ValueError(f"Insomniac at seat {seat} must use NoAction, got {action!r}")
        state.add_observation(seat, InsomniacWoke(state.current_roles[seat]))

    elif role == Role.VILLAGER:
        pass  # Villagers don't wake; this should not be called

    else:
        raise ValueError(f"Unknown role {role!r} at seat {seat}")


# ---------------------------------------------------------------------------
# Night phase orchestration
# ---------------------------------------------------------------------------

WAKE_ORDER = [Role.WEREWOLF, Role.MINION, Role.SEER, Role.ROBBER, Role.TROUBLEMAKER, Role.DRUNK, Role.INSOMNIAC]


def run_night(state: GameState, agents: dict[int, "Agent"], rng: random.Random) -> None:
    players = state.player_positions()

    for role in WAKE_ORDER:
        seats = [p for p in players if state.dealt_roles[p] == role]
        if not seats:
            continue

        for seat in seats:
            legal = get_legal_actions(state, seat)
            view = build_private_view(state, seat)
            action = agents[seat].night_action(view, legal)
            if action not in legal:
                raise ValueError(
                    f"Agent at seat {seat} returned illegal action {action!r}; "
                    f"legal={legal!r}"
                )
            apply_night_action(state, seat, action)


# ---------------------------------------------------------------------------
# Day phase orchestration
# ---------------------------------------------------------------------------

def run_day(state: GameState, agents: dict[int, "Agent"], rounds: int = 1) -> None:
    for round_num in range(1, rounds + 1):
        if rounds > 1:
            state.public_log.append(f"--- Discussion round {round_num} ---")
        for seat in state.player_positions():
            view = build_private_view(state, seat)
            statement = agents[seat].speak(view, list(state.public_log))
            state.public_log.append(f"Player {seat}: {statement}")


# ---------------------------------------------------------------------------
# Private view construction
# ---------------------------------------------------------------------------

def build_private_view(state: GameState, seat: int) -> PrivateView:
    return PrivateView(
        seat=seat,
        player_count=state.player_count,
        dealt_role=state.dealt_roles[seat],
        observations=tuple(state.observations_for(seat)),
        public_log=tuple(state.public_log),
    )


# ---------------------------------------------------------------------------
# Voting resolution
# ---------------------------------------------------------------------------

def resolve_vote(votes: dict[int, int]) -> frozenset[int]:
    """Return the set of players killed by the vote.

    A player dies only if they received the strict plurality and that count > 1.
    If all players are tied at 1 vote each, nobody dies.
    """
    if not votes:
        return frozenset()

    tally: dict[int, int] = {}
    for target in votes.values():
        tally[target] = tally.get(target, 0) + 1

    max_votes = max(tally.values())
    if max_votes <= 1:
        return frozenset()

    return frozenset(p for p, count in tally.items() if count == max_votes)


# ---------------------------------------------------------------------------
# Win evaluation
# ---------------------------------------------------------------------------

def evaluate_win(state: GameState, deaths: frozenset[int]) -> GameResult:
    players = state.player_positions()
    wolf_players = frozenset(p for p in players if state.current_roles[p] == Role.WEREWOLF)
    minion_players = frozenset(p for p in players if state.current_roles[p] == Role.MINION)

    if wolf_players:
        if deaths & wolf_players:
            # At least one werewolf died → village wins; minion loses
            village = frozenset(p for p in players if p not in wolf_players and p not in minion_players)
            return GameResult(Outcome.VILLAGE_WIN, village, deaths)
        else:
            # Wolves survive → wolf team (wolves + minion) wins
            return GameResult(Outcome.WEREWOLF_WIN, wolf_players | minion_players, deaths)
    else:
        # No werewolves among players (all in center)
        if minion_players:
            if deaths:
                # Someone died with no wolves present → minion wins
                return GameResult(Outcome.WEREWOLF_WIN, minion_players, deaths)
            else:
                # Nobody died → village correctly found no wolves; minion loses
                village = frozenset(p for p in players if p not in minion_players)
                return GameResult(Outcome.VILLAGE_WIN, village, deaths)
        else:
            if not deaths:
                return GameResult(Outcome.VILLAGE_WIN, frozenset(players), deaths)
            else:
                return GameResult(Outcome.NO_WINNER, frozenset(), deaths)
