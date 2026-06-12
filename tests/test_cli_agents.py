"""Tests for HumanAgent: stdin parsing, validation, re-prompt behaviour."""
from __future__ import annotations

from collections import deque

from onuw.agents import HumanAgent
from onuw.roles import Role
from onuw.state import (
    DrunkSwapAction,
    LoneWolfPeekAction,
    NoAction,
    PrivateView,
    RobberStealAction,
    SeerPeekPlayerAction,
)


def _view(seat: int = 0, player_count: int = 5, role: Role = Role.VILLAGER) -> PrivateView:
    return PrivateView(
        seat=seat,
        player_count=player_count,
        dealt_role=role,
        observations=(),
        public_log=(),
    )


def _agent(responses: list[str]) -> HumanAgent:
    q: deque[str] = deque(responses)
    return HumanAgent(input_fn=lambda _prompt="": q.popleft())


# ---------------------------------------------------------------------------
# night_action
# ---------------------------------------------------------------------------

class TestHumanNightAction:
    def test_single_action_returns_without_prompting(self) -> None:
        calls: list[str] = []
        agent = HumanAgent(input_fn=lambda p="": calls.append(p) or "")
        result = agent.night_action(_view(), [NoAction()])
        assert result == NoAction()
        assert calls == []  # no prompt issued

    def test_picks_first_of_multiple_actions(self) -> None:
        actions = [SeerPeekPlayerAction(1), SeerPeekPlayerAction(2)]
        agent = _agent(["1"])
        result = agent.night_action(_view(role=Role.SEER), actions)
        assert result == SeerPeekPlayerAction(1)

    def test_picks_second_of_multiple_actions(self) -> None:
        actions = [SeerPeekPlayerAction(1), SeerPeekPlayerAction(2)]
        agent = _agent(["2"])
        result = agent.night_action(_view(role=Role.SEER), actions)
        assert result == SeerPeekPlayerAction(2)

    def test_reprompts_on_out_of_range(self) -> None:
        actions = [RobberStealAction(1), RobberStealAction(2)]
        agent = _agent(["9", "0", "1"])  # 9 and 0 are invalid; 1 is valid
        result = agent.night_action(_view(role=Role.ROBBER), actions)
        assert result == RobberStealAction(1)

    def test_reprompts_on_non_integer(self) -> None:
        actions = [DrunkSwapAction(5), DrunkSwapAction(6)]
        agent = _agent(["yes", "2"])
        result = agent.night_action(_view(role=Role.DRUNK), actions)
        assert result == DrunkSwapAction(6)

    def test_lone_wolf_peek_action(self) -> None:
        actions = [LoneWolfPeekAction(5), LoneWolfPeekAction(6), LoneWolfPeekAction(7)]
        agent = _agent(["3"])
        result = agent.night_action(_view(role=Role.WEREWOLF), actions)
        assert result == LoneWolfPeekAction(7)


# ---------------------------------------------------------------------------
# speak
# ---------------------------------------------------------------------------

class TestHumanSpeak:
    def test_returns_typed_statement(self) -> None:
        agent = _agent(["I am the Seer and I saw Player 2."])
        result = agent.speak(_view(), [])
        assert result == "I am the Seer and I saw Player 2."

    def test_empty_input_returns_pass(self) -> None:
        agent = _agent([""])
        result = agent.speak(_view(), [])
        assert result == "Pass."

    def test_whitespace_only_returns_pass(self) -> None:
        agent = _agent(["   "])
        result = agent.speak(_view(), [])
        assert result == "Pass."

    def test_statement_stripped(self) -> None:
        agent = _agent(["  hello  "])
        result = agent.speak(_view(), [])
        assert result == "hello"


# ---------------------------------------------------------------------------
# vote
# ---------------------------------------------------------------------------

class TestHumanVote:
    def test_valid_vote_returned(self) -> None:
        # seat=0, player_count=5 → legal targets: 1,2,3,4
        agent = _agent(["3"])
        result = agent.vote(_view(seat=0, player_count=5), [])
        assert result == 3

    def test_cannot_vote_for_self(self) -> None:
        agent = _agent(["0", "2"])  # 0 is self → reprompt; 2 is valid
        result = agent.vote(_view(seat=0, player_count=5), [])
        assert result == 2

    def test_reprompts_on_out_of_range(self) -> None:
        agent = _agent(["99", "1"])
        result = agent.vote(_view(seat=0, player_count=5), [])
        assert result == 1

    def test_reprompts_on_non_integer(self) -> None:
        agent = _agent(["Player 1", "1"])
        result = agent.vote(_view(seat=0, player_count=5), [])
        assert result == 1

    def test_all_targets_valid_except_self(self) -> None:
        for target in [1, 2, 3, 4]:
            agent = _agent([str(target)])
            result = agent.vote(_view(seat=0, player_count=5), [])
            assert result == target
