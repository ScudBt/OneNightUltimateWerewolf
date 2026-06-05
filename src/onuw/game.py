from __future__ import annotations

import random

from onuw.agents import Agent
from onuw.engine import (
    build_private_view,
    deal,
    evaluate_win,
    resolve_vote,
    run_day,
    run_night,
)
from onuw.state import GameConfig, GameResult


def play_game(config: GameConfig, agents: dict[int, Agent]) -> GameResult:
    if set(agents.keys()) != set(range(config.player_count)):
        raise ValueError(
            f"agents keys must be exactly {{0..{config.player_count-1}}}; "
            f"got {sorted(agents.keys())!r}"
        )

    rng = random.Random(config.seed)
    state = deal(config, rng)

    run_night(state, agents, rng)
    run_day(state, agents)

    # Voting
    votes: dict[int, int] = {}
    for seat in state.player_positions():
        view = build_private_view(state, seat)
        target = agents[seat].vote(view, list(state.public_log))
        if target == seat or target not in state.player_positions():
            raise ValueError(
                f"Agent at seat {seat} cast illegal vote for {target!r}"
            )
        votes[seat] = target

    deaths = resolve_vote(votes)
    return evaluate_win(state, deaths)
