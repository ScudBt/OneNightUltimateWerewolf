from __future__ import annotations

from dataclasses import dataclass
from typing import Union

from onuw.roles import Role


@dataclass(frozen=True)
class SawWerewolves:
    wolf_positions: tuple[int, ...]


@dataclass(frozen=True)
class LoneWolfPeek:
    center_position: int
    role: Role


@dataclass(frozen=True)
class SeerPeekedPlayer:
    target: int
    role: Role


@dataclass(frozen=True)
class SeerPeekedCenter:
    targets: tuple[int, int]
    roles: tuple[Role, Role]


@dataclass(frozen=True)
class Robbed:
    target: int
    new_role: Role


@dataclass(frozen=True)
class TroublemakerSwapped:
    target_a: int
    target_b: int


@dataclass(frozen=True)
class InsomniacWoke:
    final_role: Role


Observation = Union[
    SawWerewolves,
    LoneWolfPeek,
    SeerPeekedPlayer,
    SeerPeekedCenter,
    Robbed,
    TroublemakerSwapped,
    InsomniacWoke,
]
