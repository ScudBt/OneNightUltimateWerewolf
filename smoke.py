#!/usr/bin/env python
"""
Manual smoke run: plays one ONUW game with real LLM agents and prints
every phase in detail. Each run is saved to runs/<timestamp>.txt.

Usage:
    GEMINI_API_KEY=... uv run python smoke.py
    uv run python smoke.py --seed 99 --players 4
"""
from __future__ import annotations

import argparse
import io
import random
import sys
from datetime import datetime
from pathlib import Path

from google import genai

from onuw.engine import (
    build_private_view,
    deal,
    evaluate_win,
    resolve_vote,
    run_day,
    run_night,
)
from onuw.llm_agent import LLMAgent, gemini_caller
from onuw.roles import Role
from onuw.state import GameConfig

# Default 3-player role pool: one wolf, one seer, one villager in play;
# robber, troublemaker, insomniac in the center.
ROLES_3P = (
    Role.WEREWOLF,
    Role.SEER,
    Role.VILLAGER,
    Role.ROBBER,
    Role.TROUBLEMAKER,
    Role.INSOMNIAC,
)

ROLES_4P = (
    Role.WEREWOLF,
    Role.WEREWOLF,
    Role.SEER,
    Role.ROBBER,
    Role.TROUBLEMAKER,
    Role.INSOMNIAC,
    Role.VILLAGER,
)


class _Tee(io.TextIOBase):
    """Write to two streams simultaneously."""

    def __init__(self, primary: io.TextIOBase, secondary: io.TextIOBase) -> None:
        self._primary = primary
        self._secondary = secondary

    def write(self, s: str) -> int:
        self._primary.write(s)
        self._secondary.write(s)
        return len(s)

    def flush(self) -> None:
        self._primary.flush()
        self._secondary.flush()


def sep(title: str = "") -> None:
    line = "=" * 60
    if title:
        print(f"\n{line}\n  {title}\n{line}")
    else:
        print(line)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--players", type=int, choices=[3, 4], default=3)
    parser.add_argument("--model", default="gemini-2.5-flash-lite",
                        help="Gemini model ID (default: 2.5 Flash-Lite)")
    args = parser.parse_args()

    # ── Log file setup ────────────────────────────────────────────────────────
    runs_dir = Path("runs")
    runs_dir.mkdir(exist_ok=True)
    timestamp = datetime.now().strftime("%Y-%m-%dT%H-%M-%S")
    log_path = runs_dir / f"{timestamp}.txt"
    log_file = open(log_path, "w", encoding="utf-8")

    real_stdout = sys.stdout
    sys.stdout = _Tee(real_stdout, log_file)  # type: ignore[assignment]

    try:
        _run(args, log_path)
    finally:
        sys.stdout = real_stdout
        log_file.close()

    print(f"\nRun saved to {log_path}")


def _run(args: argparse.Namespace, log_path: Path) -> None:
    roles = ROLES_3P if args.players == 3 else ROLES_4P
    config = GameConfig(player_count=args.players, roles=roles, seed=args.seed)

    print(f"seed={args.seed}  players={args.players}  model={args.model}")

    client = genai.Client()  # reads GEMINI_API_KEY (or GOOGLE_API_KEY) from env
    caller = gemini_caller(client, model=args.model)
    agents = {seat: LLMAgent(caller) for seat in range(args.players)}

    rng = random.Random(config.seed)
    state = deal(config, rng)

    # ── Deal ──────────────────────────────────────────────────────────────────
    sep("DEAL  (ground truth — agents cannot see this)")
    for pos, role in sorted(state.dealt_roles.items()):
        label = f"Player {pos}" if pos < args.players else f"Center slot {pos}"
        print(f"  {label:15s}  {role.value.upper()}")

    # ── Night ─────────────────────────────────────────────────────────────────
    sep("NIGHT PHASE")
    print("  (agents choose their night actions — calling LLM…)\n")
    run_night(state, agents, rng)

    sep("NIGHT OBSERVATIONS  (ground truth)")
    for seat in range(args.players):
        view = build_private_view(state, seat)
        print(f"  Player {seat} (dealt {view.dealt_role.value.upper()}):")
        if view.observations:
            for obs in view.observations:
                print(f"    {obs}")
        else:
            print("    (no observations)")

    # ── Day ───────────────────────────────────────────────────────────────────
    sep("DAY DISCUSSION  (each player speaks once — calling LLM…)")
    run_day(state, agents)

    # Show private reasoning alongside each player's statement
    for seat in range(args.players):
        reasoning = agents[seat].reasoning_log.get(seat, "")
        if reasoning:
            print(f"  Player {seat} [private]: {reasoning}")
        # Find this player's entry in the public log
        for entry in state.public_log:
            if entry.startswith(f"Player {seat}: "):
                print(f"  {entry}")
                break

    # ── Vote ──────────────────────────────────────────────────────────────────
    sep("VOTE  (calling LLM…)")
    votes: dict[int, int] = {}
    for seat in range(args.players):
        view = build_private_view(state, seat)
        target = agents[seat].vote(view, list(state.public_log))
        votes[seat] = target
        print(f"  Player {seat} votes for Player {target}")

    # ── Resolve ───────────────────────────────────────────────────────────────
    deaths = resolve_vote(votes)
    result = evaluate_win(state, deaths)

    sep("FINAL CARD ASSIGNMENTS  (ground truth)")
    for pos in range(args.players):
        dealt = state.dealt_roles[pos].value.upper()
        current = state.current_roles[pos].value.upper()
        tag = "  ← CHANGED" if current != dealt else ""
        print(f"  Player {pos}: dealt {dealt:12s} → final {current}{tag}")

    sep("RESULT")
    print(f"  Deaths  : {sorted(deaths) if deaths else 'nobody'}")
    print(f"  Outcome : {result.outcome.value}")
    print(f"  Winners : Players {sorted(result.winners)}")
    sep()


if __name__ == "__main__":
    main()
