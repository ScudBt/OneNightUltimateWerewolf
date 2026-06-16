"""Typed builders for the server->client and client->server messages.

Every server->client payload is built here so the network boundary is auditable
in one place: only the human's own private information and public events ever
cross the wire. Roles for *other* seats appear exclusively in the final
``reveal`` payload, after ``evaluate_win`` has run.

Client->server messages are plain dicts read in ``session.py``:
  - ``{"type": "start_game", "players": int, "seed": int|None, ...}``
  - ``{"type": "night_choice", "index": int}``
  - ``{"type": "statement", "text": str}``
  - ``{"type": "vote", "seat": int}``
"""
from __future__ import annotations

from typing import Any, Mapping, Sequence

from onuw.agents import _describe_observation
from onuw.observations import Observation
from onuw.roles import Role
from onuw.state import GameResult, Outcome

_OUTCOME_LABELS: dict[Outcome, str] = {
    Outcome.VILLAGE_WIN: "Village wins",
    Outcome.WEREWOLF_WIN: "Werewolf team wins",
    Outcome.NO_WINNER: "No winner",
}


# ---------------------------------------------------------------------------
# Server -> client events
# ---------------------------------------------------------------------------

def game_start(
    *,
    seed: int,
    player_count: int,
    provider: str,
    model: str,
    human_seat: int,
    roster: Sequence[Mapping[str, Any]],
    rounds: int,
    roles_in_deck: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    # roster entries carry name/avatar/seat only — never a role.
    return {
        "type": "game_start",
        "seed": seed,
        "player_count": player_count,
        "provider": provider,
        "model": model,
        "human_seat": human_seat,
        "players": list(roster),
        "rounds": rounds,
        "roles_in_deck": list(roles_in_deck),  # deck composition is public knowledge
    }


def your_role(role: Role, intro: str) -> dict[str, Any]:
    return {"type": "your_role", "role": role.value, "intro": intro}


def night_wake(role: Role) -> dict[str, Any]:
    return {"type": "night_wake", "role": role.value, "label": role.value.upper()}


def night_prompt(options: Sequence[str]) -> dict[str, Any]:
    # ``options`` is parallel to the seat's legal-action list; the client replies
    # with the chosen index.
    return {"type": "night_prompt", "options": list(options)}


def night_sleep(role: Role) -> dict[str, Any]:
    return {"type": "night_sleep", "role": role.value}


def night_result(
    dealt_role: Role, observations: Sequence[Observation], player_count: int
) -> dict[str, Any]:
    return {
        "type": "night_result",
        "role": dealt_role.value,
        "observations": [
            _describe_observation(o, player_count) for o in observations
        ],
    }


def round_start(round_num: int, total: int) -> dict[str, Any]:
    return {"type": "round_start", "round": round_num, "total": total}


def speaker_thinking(seat: int, name: str) -> dict[str, Any]:
    return {"type": "speaker_thinking", "seat": seat, "name": name}


def statement(seat: int, name: str, round_num: int, text: str) -> dict[str, Any]:
    return {
        "type": "statement",
        "seat": seat,
        "name": name,
        "round": round_num,
        "text": text,
    }


def round_summary(round_num: int, text: str) -> dict[str, Any]:
    return {"type": "round_summary", "round": round_num, "text": text}


def vote_prompt(targets: Sequence[int]) -> dict[str, Any]:
    return {"type": "vote_prompt", "targets": list(targets)}


def votes_revealed(votes: Mapping[int, int]) -> dict[str, Any]:
    # ``votes`` maps voter seat -> target seat. No roles involved.
    return {
        "type": "votes_revealed",
        "votes": [{"voter": v, "target": t} for v, t in sorted(votes.items())],
    }


def reveal(
    *,
    player_count: int,
    dealt: Mapping[int, Role],
    final: Mapping[int, Role],
    result: GameResult,
    human_seat: int,
    personas: Mapping[int, tuple[str, str]],
    deaths: frozenset[int],
    votes: Mapping[int, int],
    vote_reasons: Mapping[int, str],
    reactions: Mapping[int, str],
) -> dict[str, Any]:
    players = [
        {
            "seat": s,
            "name": personas[s][0],
            "avatar": personas[s][1],
            "is_human": s == human_seat,
            "dealt_role": dealt[s].value,
            "final_role": final[s].value,
            "died": s in deaths,
            # In-character reaction to the result; empty for the human, whose
            # reaction is collected after the reveal via ``human_reaction``.
            "reaction": reactions.get(s, ""),
        }
        for s in range(player_count)
    ]
    vote_list = [
        {
            "voter": v,
            "voter_name": personas[v][0],
            "voter_avatar": personas[v][1],
            "target": votes[v],
            "target_name": personas[votes[v]][0],
            "target_avatar": personas[votes[v]][1],
            "reason": vote_reasons.get(v, ""),
            "is_human": v == human_seat,
        }
        for v in sorted(votes)
    ]
    return {
        "type": "reveal",
        "players": players,
        "votes": vote_list,
        "deaths": sorted(deaths),
        "outcome": result.outcome.value,
        "outcome_label": _OUTCOME_LABELS[result.outcome],
        "winners": sorted(result.winners),
        "human_seat": human_seat,
        "human_won": human_seat in result.winners,
    }


def reactions(reactions: Mapping[int, str]) -> dict[str, Any]:
    # Streamed in after ``reveal`` so the end screen need not block on the
    # per-NPC LLM calls. Each entry fills the reaction line on an existing
    # reveal card. Built only after ``evaluate_win`` from ground truth.
    return {
        "type": "reactions",
        "reactions": [
            {"seat": s, "text": t} for s, t in sorted(reactions.items())
        ],
    }


def god_summary(text: str) -> dict[str, Any]:
    # Omniscient post-game narration. Built only after ``evaluate_win`` from
    # ground truth, so it never crosses the wire before the reveal.
    return {"type": "god_summary", "text": text}


def human_reaction(seat: int, text: str) -> dict[str, Any]:
    return {"type": "human_reaction", "seat": seat, "text": text}


def invalid_input(message: str) -> dict[str, Any]:
    return {"type": "invalid_input", "message": message}


def error(message: str) -> dict[str, Any]:
    return {"type": "error", "message": message}
