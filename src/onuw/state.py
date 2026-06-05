from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Union

from onuw.observations import Observation
from onuw.roles import Role


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class GameConfig:
    player_count: int
    roles: tuple[Role, ...]
    seed: int

    def __post_init__(self) -> None:
        if len(self.roles) != self.player_count + 3:
            raise ValueError(
                f"roles must have player_count+3 entries; "
                f"got {len(self.roles)} for player_count={self.player_count}"
            )


# ---------------------------------------------------------------------------
# Actions
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class NoAction:
    pass


@dataclass(frozen=True)
class LoneWolfPeekAction:
    center_position: int


@dataclass(frozen=True)
class SeerPeekPlayerAction:
    target: int


@dataclass(frozen=True)
class SeerPeekCenterAction:
    targets: tuple[int, int]


@dataclass(frozen=True)
class RobberStealAction:
    target: int


@dataclass(frozen=True)
class TroublemakerSwapAction:
    target_a: int
    target_b: int


Action = Union[
    NoAction,
    LoneWolfPeekAction,
    SeerPeekPlayerAction,
    SeerPeekCenterAction,
    RobberStealAction,
    TroublemakerSwapAction,
]


# ---------------------------------------------------------------------------
# Game state (engine-internal; never handed to agents)
# ---------------------------------------------------------------------------

@dataclass
class GameState:
    player_count: int
    dealt_roles: dict[int, Role]
    current_roles: dict[int, Role]
    public_log: list[str]
    _observations: dict[int, list[Observation]] = field(default_factory=dict)

    def center_positions(self) -> list[int]:
        return list(range(self.player_count, self.player_count + 3))

    def player_positions(self) -> list[int]:
        return list(range(self.player_count))

    def add_observation(self, seat: int, obs: Observation) -> None:
        self._observations.setdefault(seat, []).append(obs)

    def observations_for(self, seat: int) -> list[Observation]:
        return list(self._observations.get(seat, []))


# ---------------------------------------------------------------------------
# Private view (the only game information an agent ever receives)
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class PrivateView:
    seat: int
    player_count: int
    dealt_role: Role
    observations: tuple[Observation, ...]
    public_log: tuple[str, ...]

    def legal_vote_targets(self) -> list[int]:
        return [p for p in range(self.player_count) if p != self.seat]


# ---------------------------------------------------------------------------
# Outcome and result
# ---------------------------------------------------------------------------

class Outcome(Enum):
    VILLAGE_WIN = "village_win"
    WEREWOLF_WIN = "werewolf_win"
    NO_WINNER = "no_winner"


@dataclass(frozen=True)
class GameResult:
    outcome: Outcome
    winners: frozenset[int]
    deaths: frozenset[int]
