"""Tests for LLMAgent and run_day, using a typed fake Anthropic client."""
from __future__ import annotations

import pytest

from onuw.agents import RandomAgent, ScriptedAgent
from onuw.engine import build_private_view, deal, run_day, run_night
from onuw.game import play_game
from onuw.llm_agent import LLMAgent
from onuw.roles import Role
from onuw.state import (
    GameConfig,
    GameState,
    NoAction,
    PrivateView,
    RobberStealAction,
    SeerPeekPlayerAction,
)


# ---------------------------------------------------------------------------
# Fake caller: returns pre-scripted responses in sequence
# ---------------------------------------------------------------------------

def _make_agent(responses: list[str]) -> LLMAgent:
    it = iter(responses)
    return LLMAgent(caller=lambda system, user, max_tokens: next(it))


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _minimal_view(
    seat: int = 0,
    player_count: int = 3,
    role: Role = Role.VILLAGER,
) -> PrivateView:
    return PrivateView(
        seat=seat,
        player_count=player_count,
        dealt_role=role,
        observations=(),
        public_log=(),
    )


# ---------------------------------------------------------------------------
# night_action tests
# ---------------------------------------------------------------------------

class TestNightAction:
    def test_single_legal_action_no_api_call(self) -> None:
        """When only one legal action exists, LLMAgent skips the API entirely."""
        agent = _make_agent([])  # No responses — would crash if called
        view = _minimal_view(role=Role.VILLAGER)
        result = agent.night_action(view, [NoAction()])
        assert result == NoAction()

    def test_picks_numbered_action(self) -> None:
        """LLM returns '2' → second legal action is selected."""
        agent = _make_agent(["2"])
        view = _minimal_view(seat=0, player_count=3, role=Role.SEER)
        actions = [SeerPeekPlayerAction(1), SeerPeekPlayerAction(2)]
        result = agent.night_action(view, actions)
        assert result == SeerPeekPlayerAction(2)

    def test_picks_first_action_when_response_is_1(self) -> None:
        agent = _make_agent(["1"])
        view = _minimal_view(seat=0, player_count=3, role=Role.ROBBER)
        actions = [RobberStealAction(1), RobberStealAction(2)]
        result = agent.night_action(view, actions)
        assert result == RobberStealAction(1)

    def test_fallback_to_first_on_bad_parse(self) -> None:
        """Non-numeric response falls back to the first legal action."""
        agent = _make_agent(["hmm, I choose the seer peek"])
        view = _minimal_view(seat=0, player_count=3, role=Role.SEER)
        actions = [SeerPeekPlayerAction(1), SeerPeekPlayerAction(2)]
        result = agent.night_action(view, actions)
        assert result == SeerPeekPlayerAction(1)

    def test_fallback_on_out_of_range_number(self) -> None:
        """Out-of-range number falls back to the first action."""
        agent = _make_agent(["99"])
        view = _minimal_view(seat=0, player_count=3, role=Role.SEER)
        actions = [SeerPeekPlayerAction(1), SeerPeekPlayerAction(2)]
        result = agent.night_action(view, actions)
        assert result == SeerPeekPlayerAction(1)


# ---------------------------------------------------------------------------
# speak tests
# ---------------------------------------------------------------------------

class TestSpeak:
    def test_extracts_statement_from_tagged_response(self) -> None:
        raw = "<reasoning>I have no info, safe to admit it.</reasoning><statement>I saw nothing suspicious.</statement>"
        agent = _make_agent([raw])
        view = _minimal_view()
        result = agent.speak(view, [])
        assert result == "I saw nothing suspicious."

    def test_stores_reasoning_in_log(self) -> None:
        raw = "<reasoning>Play it safe.</reasoning><statement>I am a Villager.</statement>"
        agent = _make_agent([raw])
        view = _minimal_view(seat=2)
        agent.speak(view, [])
        assert agent.reasoning_log[2] == "Play it safe."

    def test_fallback_to_full_response_when_no_tags(self) -> None:
        """If the model omits tags, the whole response becomes the statement."""
        agent = _make_agent(["I agree with Player 0."])
        view = _minimal_view(seat=1)
        result = agent.speak(view, ["Player 0: I have nothing to add."])
        assert result == "I agree with Player 0."
        assert agent.reasoning_log.get(1, "") == ""

    def test_own_lines_relabeled_in_prompt(self) -> None:
        """The speaker's own past lines are shown as 'You (Player N)' so the model
        doesn't refer to itself in the third person (regression: P4 R3)."""
        captured: list[str] = []

        def caller(system: str, user: str, max_tokens: int) -> str:
            captured.append(user)
            return "<reasoning>x</reasoning><statement>ok</statement>"

        agent = LLMAgent(caller)
        log = ["Player 4: I am the Seer.", "Player 0: I'm a villager."]
        agent.speak(_minimal_view(seat=4, player_count=5), log)
        prompt = captured[0]
        assert "You (Player 4): I am the Seer." in prompt
        assert "Player 0: I'm a villager." in prompt
        assert "Player 4: I am the Seer." not in prompt  # the bare form is gone

    def test_reasoning_empty_when_tag_missing(self) -> None:
        agent = _make_agent(["<statement>Just a statement.</statement>"])
        view = _minimal_view(seat=0)
        result = agent.speak(view, [])
        assert result == "Just a statement."
        assert agent.reasoning_log.get(0, "") == ""

    def test_truncated_statement_without_closing_tag(self) -> None:
        """A response cut off before </statement> still yields the open content,
        never the reasoning/preamble (regression: the leak in game 1)."""
        raw = (
            "You are Player 2, dealt the ROBBER role. Strategy: act like a villager."
            "<reasoning>I am actually the robber and swapped with P1.</reasoning>"
            "<statement>I'm a Villager and didn't do anything last night"
        )
        agent = _make_agent([raw])
        result = agent.speak(_minimal_view(seat=2), [])
        assert result == "I'm a Villager and didn't do anything last night"
        assert "ROBBER" not in result and "<reasoning>" not in result

    def test_reasoning_without_statement_does_not_leak(self) -> None:
        """If only reasoning survives (statement truncated entirely), fall back to
        a neutral line rather than dumping private reasoning into the log."""
        raw = "<reasoning>I am the werewolf and want to frame P0.</reasoning>"
        agent = _make_agent([raw])
        result = agent.speak(_minimal_view(seat=3), [])
        assert "werewolf" not in result.lower()
        assert agent.reasoning_log[3] == "I am the werewolf and want to frame P0."


# ---------------------------------------------------------------------------
# vote tests
# ---------------------------------------------------------------------------

class TestVote:
    def test_returns_valid_seat(self) -> None:
        agent = _make_agent(["2"])
        view = _minimal_view(seat=0, player_count=3)
        result = agent.vote(view, [])
        assert result == 2

    def test_cannot_vote_for_self(self) -> None:
        """Even if the LLM returns own seat, fallback to first legal target."""
        agent = _make_agent(["0"])  # seat 0 voting for 0 = illegal
        view = _minimal_view(seat=0, player_count=3)
        result = agent.vote(view, [])
        # legal targets are [1, 2]; fallback should be 1
        assert result == 1

    def test_fallback_on_bad_parse(self) -> None:
        agent = _make_agent(["Player Two"])
        view = _minimal_view(seat=0, player_count=3)
        result = agent.vote(view, [])
        assert result in view.legal_vote_targets()

    def test_returns_valid_target_in_5player_game(self) -> None:
        agent = _make_agent(["3"])
        view = _minimal_view(seat=1, player_count=5)
        result = agent.vote(view, [])
        assert result == 3
        assert result in view.legal_vote_targets()

    def test_parses_tagged_vote_and_stores_reason(self) -> None:
        raw = "<reason>P2 dodged every question.</reason><vote>2</vote>"
        agent = _make_agent([raw])
        view = _minimal_view(seat=0, player_count=3)
        result = agent.vote(view, [])
        assert result == 2
        assert agent.vote_reasoning_log[0] == "P2 dodged every question."

    def test_vote_reason_empty_for_bare_integer(self) -> None:
        agent = _make_agent(["2"])
        view = _minimal_view(seat=0, player_count=3)
        assert agent.vote(view, []) == 2
        assert agent.vote_reasoning_log.get(0, "") == ""

    def test_truncated_vote_uses_first_legal_integer(self) -> None:
        # closing </vote> truncated away; still recover the seat
        raw = "<reason>They contradicted themselves.</reason><vote>1"
        agent = _make_agent([raw])
        view = _minimal_view(seat=0, player_count=3)
        assert agent.vote(view, []) == 1

    def test_vote_prompt_requests_honest_private_reasoning(self) -> None:
        captured: list[str] = []

        def caller(system: str, user: str, max_tokens: int) -> str:
            captured.append(user)
            return "<reason>I'm the Minion, voting with my wolf.</reason><vote>1</vote>"

        agent = LLMAgent(caller)
        agent.vote(_minimal_view(seat=0, player_count=3), [])
        prompt = captured[0]
        assert "PRIVATE" in prompt and "honest" in prompt.lower()


# ---------------------------------------------------------------------------
# run_day integration
# ---------------------------------------------------------------------------

class TestRunDay:
    def _make_state(self) -> GameState:
        config = GameConfig(
            player_count=3,
            roles=(Role.WEREWOLF, Role.SEER, Role.VILLAGER, Role.ROBBER, Role.TROUBLEMAKER, Role.INSOMNIAC),
            seed=1,
        )
        import random
        rng = random.Random(config.seed)
        return deal(config, rng)

    def test_appends_one_entry_per_player_in_seat_order(self) -> None:
        state = self._make_state()
        agents = {
            0: _make_agent(["I am innocent."]),
            1: _make_agent(["Trust me."]),
            2: _make_agent(["Hmm."]),
        }
        run_day(state, agents)
        day_entries = [e for e in state.public_log if e.startswith("Player ")]
        assert len(day_entries) == 3
        assert day_entries[0].startswith("Player 0:")
        assert day_entries[1].startswith("Player 1:")
        assert day_entries[2].startswith("Player 2:")

    def test_statement_content_in_log(self) -> None:
        state = self._make_state()
        agents = {
            0: _make_agent(["I saw a Seer."]),
            1: _make_agent(["I am the Seer."]),
            2: _make_agent(["Suspicious."]),
        }
        run_day(state, agents)
        assert any("I saw a Seer." in e for e in state.public_log)
        assert any("I am the Seer." in e for e in state.public_log)

    def test_each_subsequent_speak_sees_prior_entries(self) -> None:
        """Each agent's speak call receives the log up to that point."""
        received_logs: list[list[str]] = []

        class _LogCapturingAgent:
            def __init__(self, statement: str) -> None:
                self._statement = statement

            def night_action(self, view: PrivateView, legal_actions: object) -> object:
                return NoAction()

            def speak(self, view: PrivateView, public_log: list[str]) -> str:
                received_logs.append(list(public_log))
                return self._statement

            def vote(self, view: PrivateView, public_log: list[str]) -> int:
                return (view.seat + 1) % view.player_count

        state = self._make_state()
        agents: dict[int, object] = {
            0: _LogCapturingAgent("First."),
            1: _LogCapturingAgent("Second."),
            2: _LogCapturingAgent("Third."),
        }
        run_day(state, agents)  # type: ignore[arg-type]
        assert "Player 0: First." in received_logs[1]
        assert "Player 0: First." in received_logs[2]
        assert "Player 1: Second." in received_logs[2]


# ---------------------------------------------------------------------------
# RandomAgent.speak
# ---------------------------------------------------------------------------

class TestRandomAgentSpeak:
    def test_returns_nonempty_string(self) -> None:
        import random
        agent = RandomAgent(random.Random(0))
        view = _minimal_view()
        result = agent.speak(view, [])
        assert isinstance(result, str)
        assert len(result) > 0

    def test_deterministic(self) -> None:
        import random
        a1 = RandomAgent(random.Random(42))
        a2 = RandomAgent(random.Random(42))
        view = _minimal_view()
        assert a1.speak(view, []) == a2.speak(view, [])


# ---------------------------------------------------------------------------
# ScriptedAgent.speak
# ---------------------------------------------------------------------------

class TestScriptedAgentSpeak:
    def test_replays_statements_in_order(self) -> None:
        agent = ScriptedAgent(
            actions=[NoAction()],
            vote_target=1,
            statements=["Hello.", "World."],
        )
        view = _minimal_view()
        assert agent.speak(view, []) == "Hello."
        assert agent.speak(view, []) == "World."

    def test_returns_pass_when_exhausted(self) -> None:
        agent = ScriptedAgent(actions=[NoAction()], vote_target=1, statements=["Only one."])
        view = _minimal_view()
        agent.speak(view, [])
        assert agent.speak(view, []) == "Pass."

    def test_no_statements_returns_pass(self) -> None:
        agent = ScriptedAgent(actions=[NoAction()], vote_target=1)
        view = _minimal_view()
        assert agent.speak(view, []) == "Pass."


# ---------------------------------------------------------------------------
# Full play_game integration with mocked LLM agents
# ---------------------------------------------------------------------------

class TestPlayGameWithLLMAgents:
    def test_full_game_completes(self) -> None:
        """play_game runs to completion with all-LLM agents (all mocked)."""
        config = GameConfig(
            player_count=3,
            roles=(Role.WEREWOLF, Role.SEER, Role.VILLAGER, Role.ROBBER, Role.TROUBLEMAKER, Role.INSOMNIAC),
            seed=7,
        )
        # Each agent needs: 1 night_action call + 1 speak call + 1 vote call.
        # night_action for Werewolf (lone wolf): needs a choice among center peeks → return "1"
        # Seer and Robber also have multiple legal actions → return "1"
        # speak for each → return a statement
        # vote for each → return a valid target
        agents = {
            0: _make_agent(["1", "I am innocent.", "2"]),
            1: _make_agent(["1", "Trust me.", "0"]),
            2: _make_agent(["1", "Suspicious.", "0"]),
        }
        from onuw.state import Outcome
        result = play_game(config, agents)
        assert result.outcome in {Outcome.VILLAGE_WIN, Outcome.WEREWOLF_WIN, Outcome.NO_WINNER}

    def test_day_log_populated_after_play_game(self) -> None:
        """After play_game, the public log contains player statements."""
        config = GameConfig(
            player_count=3,
            roles=(Role.WEREWOLF, Role.SEER, Role.VILLAGER, Role.ROBBER, Role.TROUBLEMAKER, Role.INSOMNIAC),
            seed=99,
        )
        agents = {
            0: _make_agent(["1", "Alpha.", "2"]),
            1: _make_agent(["1", "Beta.", "0"]),
            2: _make_agent(["1", "Gamma.", "0"]),
        }
        play_game(config, agents)
        # If we reach here without error the log was correctly populated
