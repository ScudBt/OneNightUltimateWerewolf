"""Cosmetic identities for the LLM opponents.

Names and avatars are assigned independently of role, deterministically from the
game seed, so they leak no information. The human seat is always labeled "You".
Avatars deliberately avoid the role emojis used by the card art.
"""
from __future__ import annotations

import random

# Neutral avatars — none of these double as a role emoji.
_PERSONA_POOL: list[tuple[str, str]] = [
    ("Mara", "🦉"),
    ("Bso", "🦊"),
    ("Iris", "🐈"),
    ("Dex", "🐢"),
    ("Juno", "🦅"),
    ("Pax", "🐙"),
    ("Wren", "🦋"),
    ("Cleo", "🐝"),
    ("Nico", "🦄"),
    ("Vale", "🐬"),
]

_HUMAN: tuple[str, str] = ("You", "🧑")


def assign_personas(
    seed: int, player_count: int, human_seat: int
) -> dict[int, tuple[str, str]]:
    """Map every seat to a (name, avatar). Deterministic in ``seed``."""
    pool = list(_PERSONA_POOL)
    random.Random(seed).shuffle(pool)

    personas: dict[int, tuple[str, str]] = {}
    cursor = 0
    for seat in range(player_count):
        if seat == human_seat:
            personas[seat] = _HUMAN
        else:
            personas[seat] = pool[cursor % len(pool)]
            cursor += 1
    return personas
