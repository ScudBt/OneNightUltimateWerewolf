"""Interactive CLI for One Night Ultimate Werewolf.

Usage:
    python -m onuw.cli [--players N] [--seed SEED] [--provider gemini|anthropic] [--model MODEL]

Player 0 is always the human. Seats 1..N-1 are LLM agents.
All output is tee'd to runs/<timestamp>.txt.
"""
from __future__ import annotations

import argparse
import io
import random
import sys
from datetime import datetime
from pathlib import Path

from onuw._env import load_env
from onuw.agents import HumanAgent
from onuw.engine import (
    WAKE_ORDER,
    apply_night_action,
    build_private_view,
    deal,
    evaluate_win,
    get_legal_actions,
    resolve_vote,
)
from onuw.llm_agent import LLMAgent, LLMCaller, anthropic_caller, gemini_caller
from onuw.presets import PRESETS
from onuw.roles import Role
from onuw.state import GameConfig, Outcome

_ROLE_INTROS: dict[Role, str] = {
    Role.WEREWOLF: (
        "You are a WEREWOLF. Avoid being eliminated. "
        "You'll learn who the other Werewolves are tonight."
    ),
    Role.MINION: (
        "You are the MINION (Werewolf team). "
        "You'll learn who the Werewolves are tonight — they don't see you. "
        "Win by keeping the Werewolves alive, or by causing any death if there are no Werewolves among players."
    ),
    Role.SEER: (
        "You are the SEER. "
        "Tonight you may peek at one player's card or two center cards."
    ),
    Role.ROBBER: (
        "You are the ROBBER. "
        "Tonight you swap your card with another player's card and see what you got."
    ),
    Role.TROUBLEMAKER: (
        "You are the TROUBLEMAKER. "
        "Tonight you swap two other players' cards without seeing them."
    ),
    Role.DRUNK: (
        "You are the DRUNK. "
        "Tonight you swap your card with a center card — you won't know your new role."
    ),
    Role.INSOMNIAC: (
        "You are the INSOMNIAC. "
        "After all night actions you'll wake and see your current card."
    ),
    Role.VILLAGER: (
        "You are a VILLAGER. You have no night action. "
        "Use the discussion to find the Werewolves."
    ),
}


# ---------------------------------------------------------------------------
# Tee: write stdout to both terminal and log file
# ---------------------------------------------------------------------------

class _Tee(io.TextIOBase):
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


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _sep(title: str = "") -> None:
    width = 50
    if title:
        side = (width - len(title) - 2) // 2
        print(f"\n{'=' * side} {title} {'=' * side}")
    else:
        print(f"\n{'=' * width}")


def _role_name(role: Role) -> str:
    return role.value.upper()


def _make_caller(provider: str, model: str) -> LLMCaller:
    try:
        if provider == "anthropic":
            import anthropic as _anthropic
            return anthropic_caller(_anthropic.Anthropic(), model)
        else:
            from google import genai as _genai
            return gemini_caller(_genai.Client(), model)
    except Exception as exc:
        print(f"Failed to initialise {provider} client: {exc}", file=sys.stderr)
        sys.exit(1)


# ---------------------------------------------------------------------------
# Game
# ---------------------------------------------------------------------------

def run_game(player_count: int, seed: int, provider: str, model: str, log_file: io.TextIOBase) -> None:
    roles = PRESETS[player_count]
    config = GameConfig(player_count=player_count, roles=roles, seed=seed)
    rng = random.Random(seed)
    human_seat = rng.randint(0, player_count - 1)
    state = deal(config, rng)

    caller = _make_caller(provider, model)

    human = HumanAgent()
    agents: dict[int, object] = {}
    for seat in range(player_count):
        agents[seat] = human if seat == human_seat else LLMAgent(caller)

    # Welcome
    _sep("ONE NIGHT ULTIMATE WEREWOLF")
    print(f"Seed : {seed}  |  Players: {player_count}  |  Provider: {provider} ({model})")
    roles_in_deck = sorted(set(r.value for r in roles))
    print(f"Roles in deck: {', '.join(roles_in_deck)}")
    print(f"You are Player {human_seat}.")

    human_role = state.dealt_roles[human_seat]
    _sep("YOUR ROLE")
    print(_ROLE_INTROS[human_role])

    input("\nPress Enter when you are ready to start the night phase...")

    # -----------------------------------------------------------------------
    # Night phase
    # -----------------------------------------------------------------------
    _sep("NIGHT")
    print("Night falls. All players close their eyes.\n")

    for role in WAKE_ORDER:
        seats_with_role = [p for p in state.player_positions() if state.dealt_roles[p] == role]
        if not seats_with_role:
            continue

        role_label = _role_name(role)
        print(f"[The {role_label} wakes...]")

        for seat in seats_with_role:
            legal = get_legal_actions(state, seat)
            view = build_private_view(state, seat)
            action = agents[seat].night_action(view, legal)  # type: ignore[union-attr]
            if seat == human_seat and len(legal) > 1:
                print(f"  -> You chose: {action!r}")
            apply_night_action(state, seat, action)

        print(f"[The {role_label} goes back to sleep.]\n")

    print("Morning arrives. Everyone opens their eyes.\n")

    human_view = build_private_view(state, human_seat)
    human.show_night_result(human_view)

    input("\nPress Enter to begin the discussion...")

    # -----------------------------------------------------------------------
    # Discussion — 3 rounds
    # -----------------------------------------------------------------------
    _sep("DISCUSSION")
    print("Each player speaks once per round. 3 rounds total.\n")

    for round_num in range(1, 4):
        header = f"--- Round {round_num} ---"
        print(f"\n{header}")
        state.public_log.append(header)

        for seat in state.player_positions():
            view = build_private_view(state, seat)
            if seat == human_seat:
                print(f"\nPlayer {seat} (you) — your turn to speak.")
            else:
                print(f"\nPlayer {seat} is thinking...")

            statement = agents[seat].speak(view, list(state.public_log))  # type: ignore[union-attr]
            state.public_log.append(f"Player {seat}: {statement}")

            if seat != human_seat:
                agent = agents[seat]
                if isinstance(agent, LLMAgent):
                    reasoning = agent.reasoning_log.get(seat, "")
                    if reasoning:
                        log_file.write(f"  [Player {seat} private]: {reasoning}\n")
                        log_file.flush()

            print(f"Player {seat}: {statement}")

    # -----------------------------------------------------------------------
    # Voting
    # -----------------------------------------------------------------------
    _sep("VOTING")
    print("Votes are collected; all revealed simultaneously.\n")

    votes: dict[int, int] = {}

    human_view = build_private_view(state, human_seat)
    votes[human_seat] = human.vote(human_view, list(state.public_log))

    for seat in state.player_positions():
        if seat == human_seat:
            continue
        view = build_private_view(state, seat)
        votes[seat] = agents[seat].vote(view, list(state.public_log))  # type: ignore[union-attr]

    print("\nAll votes:")
    for voter in sorted(votes):
        you = " (you)" if voter == human_seat else ""
        print(f"  Player {voter}{you}  →  Player {votes[voter]}")

    # -----------------------------------------------------------------------
    # Results
    # -----------------------------------------------------------------------
    deaths = resolve_vote(votes)
    result = evaluate_win(state, deaths)

    _sep("RESULTS")

    if deaths:
        died_str = ", ".join(f"Player {d}" for d in sorted(deaths))
        print(f"Eliminated: {died_str}")
        for d in sorted(deaths):
            print(f"  Player {d} held: {_role_name(state.current_roles[d])}")
    else:
        print("Nobody was eliminated.")

    print("\nFinal cards (after all night swaps):")
    for seat in state.player_positions():
        you = " (you)" if seat == human_seat else ""
        dealt = _role_name(state.dealt_roles[seat])
        final = _role_name(state.current_roles[seat])
        swap_note = f"  [dealt {dealt}]" if final != dealt else ""
        print(f"  Player {seat}{you}: {final}{swap_note}")

    outcome_label = {
        Outcome.VILLAGE_WIN: "VILLAGE WINS",
        Outcome.WEREWOLF_WIN: "WEREWOLF TEAM WINS",
        Outcome.NO_WINNER: "NO WINNER",
    }[result.outcome]
    print(f"\n{outcome_label}")

    if result.winners:
        winners_str = ", ".join(f"Player {w}" for w in sorted(result.winners))
        print(f"Winners: {winners_str}")

    you_won = human_seat in result.winners
    print("\n>>> You WON! <<<" if you_won else "\n>>> You lost. <<<")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

_DEFAULT_MODELS = {
    "gemini": "gemini-2.5-flash-lite",
    "anthropic": "claude-sonnet-4-6",
}


def main() -> None:
    load_env()  # read ANTHROPIC_API_KEY / GEMINI_API_KEY from repo-root .env
    parser = argparse.ArgumentParser(description="One Night Ultimate Werewolf (CLI)")
    parser.add_argument(
        "--players", type=int, default=5,
        choices=sorted(PRESETS.keys()),
        help="Number of players (default: 5)",
    )
    parser.add_argument("--seed", type=int, default=None, help="RNG seed (random if omitted)")
    parser.add_argument(
        "--provider", default="gemini", choices=["gemini", "anthropic"],
        help="LLM provider for NPC agents (default: gemini)",
    )
    parser.add_argument(
        "--model", default=None,
        help="Model ID override (defaults to gemini-2.5-flash-lite or claude-sonnet-4-6)",
    )
    args = parser.parse_args()

    provider = args.provider
    model = args.model or _DEFAULT_MODELS[provider]
    seed = args.seed if args.seed is not None else random.randint(0, 2**31)

    # Tee all output to a timestamped run file
    Path("runs").mkdir(exist_ok=True)
    timestamp = datetime.now().strftime("%Y-%m-%dT%H-%M-%S")
    log_path = Path("runs") / f"{timestamp}.txt"
    log_file = open(log_path, "w", encoding="utf-8")

    real_stdout = sys.stdout
    sys.stdout = _Tee(real_stdout, log_file)  # type: ignore[assignment]
    try:
        run_game(player_count=args.players, seed=seed, provider=provider, model=model, log_file=log_file)
    finally:
        sys.stdout = real_stdout
        log_file.close()

    print(f"\nRun saved to {log_path}")


if __name__ == "__main__":
    main()
