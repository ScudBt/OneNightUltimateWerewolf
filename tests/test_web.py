"""Tests for the Phase 3 web layer.

Focus: the network boundary (no role/reasoning leakage), the async
``WebHumanAgent``, full-game orchestration + reproducibility, persona
assignment, and round-summary gating. No browser, no real LLM, no network.
"""
from __future__ import annotations

import asyncio
import random
from typing import Any

from onuw.agents import RandomAgent
from onuw.roles import Role
from onuw.state import (
    Action,
    NoAction,
    PrivateView,
    SeerPeekPlayerAction,
)
from onuw.web.personas import assign_personas
from onuw.web.session import GameOptions, GameSession, WebHumanAgent

# ---------------------------------------------------------------------------
# Test doubles
# ---------------------------------------------------------------------------

def _view(seat: int = 0, player_count: int = 5, role: Role = Role.SEER) -> PrivateView:
    return PrivateView(
        seat=seat,
        player_count=player_count,
        dealt_role=role,
        observations=(),
        public_log=(),
    )


def _fake_caller(system: str, user: str, max_tokens: int) -> str:
    return "a neutral recap"


class AutoHuman:
    """Async human interface that plays at random (for headless full games)."""

    def __init__(
        self, rng: random.Random, *, vote_reason: str = "", reaction: str = ""
    ) -> None:
        self._r = RandomAgent(rng)
        self.last_vote_reason = vote_reason
        self._reaction = reaction

    async def night_action(self, view: PrivateView, legal: list[Any]) -> Any:
        return self._r.night_action(view, legal)

    async def speak(self, view: PrivateView, public_log: list[str]) -> str:
        return self._r.speak(view, public_log)

    async def vote(self, view: PrivateView, public_log: list[str]) -> int:
        return self._r.vote(view, public_log)

    async def react(self) -> str:
        return self._reaction


def _factory(seat: int, role: Role) -> RandomAgent:
    return RandomAgent(random.Random(1000 + seat))


def _run_session(
    *,
    summaries: bool,
    caller: Any = None,
    seed: int = 42,
    human: Any = None,
) -> tuple[GameSession, list[dict[str, Any]]]:
    sent: list[dict[str, Any]] = []

    async def send(msg: dict[str, Any]) -> None:
        sent.append(msg)

    async def go() -> GameSession:
        queue: "asyncio.Queue[dict[str, Any]]" = asyncio.Queue()
        session = GameSession(
            send,
            queue,
            GameOptions(player_count=5, seed=seed, summaries=summaries),
            caller=caller,
            agent_factory=_factory,
            human_agent=human or AutoHuman(random.Random(7)),
        )
        await session.run()
        return session

    session = asyncio.run(go())
    return session, sent


# ---------------------------------------------------------------------------
# WebHumanAgent
# ---------------------------------------------------------------------------

class TestWebHumanAgent:
    def _make(self, messages: list[dict[str, Any]]) -> tuple[
        WebHumanAgent, "asyncio.Queue[dict[str, Any]]", list[dict[str, Any]]
    ]:
        sent: list[dict[str, Any]] = []

        async def send(msg: dict[str, Any]) -> None:
            sent.append(msg)

        queue: "asyncio.Queue[dict[str, Any]]" = asyncio.Queue()
        for m in messages:
            queue.put_nowait(m)
        return WebHumanAgent(send, queue), queue, sent

    def test_single_action_returns_without_prompt(self) -> None:
        agent, _q, sent = self._make([])
        result = asyncio.run(agent.night_action(_view(), [NoAction()]))
        assert result == NoAction()
        assert sent == []  # no prompt emitted

    def test_night_choice_index(self) -> None:
        legal: list[Action] = [SeerPeekPlayerAction(1), SeerPeekPlayerAction(2)]
        agent, _q, sent = self._make([{"type": "night_choice", "index": 1}])
        result = asyncio.run(agent.night_action(_view(), legal))
        assert result == SeerPeekPlayerAction(2)
        assert any(m["type"] == "night_prompt" for m in sent)

    def test_night_choice_invalid_then_valid(self) -> None:
        legal: list[Action] = [SeerPeekPlayerAction(1), SeerPeekPlayerAction(2)]
        agent, _q, sent = self._make(
            [{"type": "night_choice", "index": 9}, {"type": "night_choice", "index": 0}]
        )
        result = asyncio.run(agent.night_action(_view(), legal))
        assert result == SeerPeekPlayerAction(1)
        assert any(m["type"] == "invalid_input" for m in sent)

    def test_speak_returns_text(self) -> None:
        agent, _q, _sent = self._make([{"type": "statement", "text": "  hi there  "}])
        assert asyncio.run(agent.speak(_view(), [])) == "hi there"

    def test_speak_empty_is_pass(self) -> None:
        agent, _q, _sent = self._make([{"type": "statement", "text": "   "}])
        assert asyncio.run(agent.speak(_view(), [])) == "Pass."

    def test_vote_valid(self) -> None:
        agent, _q, sent = self._make([{"type": "vote", "seat": 3}])
        assert asyncio.run(agent.vote(_view(seat=0, player_count=5), [])) == 3
        assert any(m["type"] == "vote_prompt" for m in sent)

    def test_vote_self_then_valid(self) -> None:
        agent, _q, sent = self._make(
            [{"type": "vote", "seat": 0}, {"type": "vote", "seat": 2}]
        )
        assert asyncio.run(agent.vote(_view(seat=0, player_count=5), [])) == 2
        assert any(m["type"] == "invalid_input" for m in sent)

    def test_vote_captures_optional_reason(self) -> None:
        agent, _q, _sent = self._make(
            [{"type": "vote", "seat": 3, "reason": "  shifty story  "}]
        )
        assert asyncio.run(agent.vote(_view(seat=0, player_count=5), [])) == 3
        assert agent.last_vote_reason == "shifty story"

    def test_vote_without_reason_is_empty(self) -> None:
        agent, _q, _sent = self._make([{"type": "vote", "seat": 3}])
        asyncio.run(agent.vote(_view(seat=0, player_count=5), []))
        assert agent.last_vote_reason == ""

    def test_react_returns_text(self) -> None:
        agent, _q, _sent = self._make([{"type": "reaction", "text": "  knew it!  "}])
        assert asyncio.run(agent.react()) == "knew it!"


# ---------------------------------------------------------------------------
# Personas
# ---------------------------------------------------------------------------

class TestPersonas:
    def test_deterministic(self) -> None:
        a = assign_personas(123, 5, human_seat=2)
        b = assign_personas(123, 5, human_seat=2)
        assert a == b

    def test_human_seat_labeled_you(self) -> None:
        p = assign_personas(123, 5, human_seat=2)
        assert p[2] == ("You", "🧑")
        for seat, (name, _avatar) in p.items():
            if seat != 2:
                assert name != "You"


# ---------------------------------------------------------------------------
# Full-game orchestration + boundary
# ---------------------------------------------------------------------------

class TestSessionFlow:
    def test_runs_to_reveal_matching_engine(self) -> None:
        session, sent = _run_session(summaries=False)
        reveals = [m for m in sent if m["type"] == "reveal"]
        assert len(reveals) == 1
        reveal = reveals[0]
        assert session.result is not None and session.state is not None
        # Reveal outcome and final roles must match engine ground truth.
        assert reveal["outcome"] == session.result.outcome.value
        for p in reveal["players"]:
            assert p["final_role"] == session.state.current_roles[p["seat"]].value
            assert p["dealt_role"] == session.state.dealt_roles[p["seat"]].value

    def test_game_start_reports_role_counts(self) -> None:
        _session, sent = _run_session(summaries=False)
        gs = [m for m in sent if m["type"] == "game_start"][0]
        deck = {r["role"]: r["count"] for r in gs["roles_in_deck"]}
        assert deck["werewolf"] == 2  # 5-player preset has two wolves
        assert all(c >= 1 for c in deck.values())

    def test_reproducible(self) -> None:
        s1, e1 = _run_session(summaries=False)
        s2, e2 = _run_session(summaries=False)
        v1 = [m for m in e1 if m["type"] == "votes_revealed"][0]
        v2 = [m for m in e2 if m["type"] == "votes_revealed"][0]
        assert v1 == v2
        assert s1.result is not None and s2.result is not None
        assert s1.result.outcome == s2.result.outcome
        assert s1.result.winners == s2.result.winners

    def test_reveal_includes_vote_breakdown(self) -> None:
        session, sent = _run_session(summaries=False)
        reveal = [m for m in sent if m["type"] == "reveal"][0]
        assert "votes" in reveal
        # one entry per player, each with voter/target seats
        assert len(reveal["votes"]) == 5
        for v in reveal["votes"]:
            assert v["voter"] in range(5) and v["target"] in range(5)
            assert v["target"] != v["voter"]
            assert "reason" in v

    def test_vote_reasons_captured_from_llm_agents(self) -> None:
        # LLM-style agents return a tagged vote; the reason must reach the reveal.
        def llm_factory(seat: int, role: Role):
            from onuw.llm_agent import LLMAgent
            return LLMAgent(lambda s, u, n: "<reason>they seemed shifty</reason><vote>0</vote>")

        sent: list[Any] = []

        async def send(msg: Any) -> None:
            sent.append(msg)

        async def go() -> None:
            q: "asyncio.Queue[Any]" = asyncio.Queue()
            session = GameSession(
                send, q, GameOptions(player_count=5, seed=42, summaries=False),
                agent_factory=llm_factory, human_agent=AutoHuman(random.Random(7)),
            )
            await session.run()

        asyncio.run(go())
        reveal = [m for m in sent if m["type"] == "reveal"][0]
        npc_reasons = [v["reason"] for v in reveal["votes"] if not v["is_human"]]
        assert npc_reasons and all(r == "they seemed shifty" for r in npc_reasons)

    def test_human_vote_reason_reaches_reveal(self) -> None:
        human = AutoHuman(random.Random(7), vote_reason="they bluffed Seer")
        session, sent = _run_session(summaries=False, human=human)
        reveal = [m for m in sent if m["type"] == "reveal"][0]
        human_vote = [v for v in reveal["votes"] if v["is_human"]][0]
        assert human_vote["reason"] == "they bluffed Seer"

    def test_god_summary_emitted_with_caller_after_reveal(self) -> None:
        _session, sent = _run_session(summaries=False, caller=_fake_caller)
        types = [m["type"] for m in sent]
        assert "god_summary" in types
        god = [m for m in sent if m["type"] == "god_summary"][0]
        assert god["text"] == "a neutral recap"
        # God view is omniscient, so it must never precede the reveal.
        assert types.index("god_summary") > types.index("reveal")

    def test_god_summary_skipped_without_caller(self) -> None:
        _session, sent = _run_session(summaries=False, caller=None)
        assert not any(m["type"] == "god_summary" for m in sent)

    def test_npc_reactions_populated_with_caller(self) -> None:
        # Reactions are now streamed in a separate message after the reveal,
        # so the end screen need not block on the per-NPC LLM calls.
        session, sent = _run_session(summaries=False, caller=_fake_caller)
        types = [m["type"] for m in sent]
        reveal = [m for m in sent if m["type"] == "reveal"][0]
        reactions = [m for m in sent if m["type"] == "reactions"][0]
        # The reveal must be sent before reactions are computed.
        assert types.index("reveal") < types.index("reactions")
        # The reveal payload itself no longer carries any reaction text.
        assert all(p["reaction"] == "" for p in reveal["players"])
        human_seat = session.human_seat
        reacted = {r["seat"]: r["text"] for r in reactions["reactions"]}
        assert human_seat not in reacted  # human reacts after the reveal
        assert reacted  # every NPC seat is present
        assert all(text == "a neutral recap" for text in reacted.values())

    def test_npc_reactions_empty_without_caller(self) -> None:
        _session, sent = _run_session(summaries=False, caller=None)
        reveal = [m for m in sent if m["type"] == "reveal"][0]
        assert all(p["reaction"] == "" for p in reveal["players"])
        assert not any(m["type"] == "reactions" for m in sent)

    def test_human_reaction_event_emitted(self) -> None:
        human = AutoHuman(random.Random(7), reaction="robbed by Mona!")
        session, sent = _run_session(summaries=False, human=human)
        hr = [m for m in sent if m["type"] == "human_reaction"]
        assert len(hr) == 1
        assert hr[0]["seat"] == session.human_seat
        assert hr[0]["text"] == "robbed by Mona!"

    def test_empty_human_reaction_emits_no_event(self) -> None:
        _session, sent = _run_session(summaries=False, human=AutoHuman(random.Random(7)))
        assert not any(m["type"] == "human_reaction" for m in sent)

    def test_no_role_leak_before_reveal(self) -> None:
        session, sent = _run_session(summaries=False)
        human_seat = session.human_seat
        for m in sent:
            if m["type"] == "reveal":
                continue
            # No event before reveal carries another player's role or any reasoning.
            assert "reasoning" not in m
            if m["type"] == "your_role":
                continue  # the human's OWN role — allowed
            if m["type"] == "night_result":
                # Only the human's own dealt role appears here.
                assert m["role"] == session.state.dealt_roles[human_seat].value  # type: ignore[union-attr]
                continue
            if m["type"] == "game_start":
                for entry in m["players"]:
                    assert "role" not in entry
                    assert "dealt_role" not in entry
                    assert "final_role" not in entry
                continue
            # Generic events (statement, votes_revealed, round_start, ...) carry no roles.
            assert "role" not in m or m["type"] in ("night_wake", "night_sleep")

    def test_night_wake_only_names_role_not_holder(self) -> None:
        # night_wake/night_sleep narrate the role waking but never WHO holds it.
        session, sent = _run_session(summaries=False)
        for m in sent:
            if m["type"] in ("night_wake", "night_sleep"):
                assert "seat" not in m

    def test_wake_events_cover_every_deck_role_not_just_held(self) -> None:
        # The narrator must call every waking role in the deck, even when all
        # its copies are in the center. Suppressing the call would leak hidden
        # card locations (e.g. an omitted Werewolf wake proves no player holds
        # one). Seed 0 leaves the Seer and Insomniac entirely in the center.
        from onuw.engine import WAKE_ORDER

        session, sent = _run_session(summaries=False, seed=0)
        assert session.state is not None
        deck_roles = set(session.state.dealt_roles.values())
        held_roles = {
            session.state.dealt_roles[p] for p in session.state.player_positions()
        }
        expected_wakes = [r.value for r in WAKE_ORDER if r in deck_roles]
        center_only = [r for r in WAKE_ORDER if r in deck_roles and r not in held_roles]
        assert center_only, "seed 0 should leave a waking role only in the center"

        woke = [m["role"] for m in sent if m["type"] == "night_wake"]
        slept = [m["role"] for m in sent if m["type"] == "night_sleep"]
        assert woke == expected_wakes
        assert slept == expected_wakes
        for role in center_only:
            assert role.value in woke


# ---------------------------------------------------------------------------
# Round summaries (flagged)
# ---------------------------------------------------------------------------

class TestModelSelection:
    """The --model flag (app.state.model) must actually reach the game."""

    def _with_server_default(self, provider: str, model: str):
        from onuw.web import server
        server.app.state.provider = provider
        server.app.state.model = model
        return server

    def test_server_model_default_used_when_client_omits(self) -> None:
        server = self._with_server_default("gemini", "gemini-3.5-flash")
        opts = server._options_from_start({"type": "start_game", "provider": "gemini"})
        assert opts.model == "gemini-3.5-flash"

    def test_client_model_overrides(self) -> None:
        server = self._with_server_default("gemini", "gemini-3.5-flash")
        opts = server._options_from_start(
            {"type": "start_game", "provider": "gemini", "model": "gemini-3.1-flash-lite"}
        )
        assert opts.model == "gemini-3.1-flash-lite"

    def test_other_provider_falls_back_to_its_default(self) -> None:
        server = self._with_server_default("gemini", "gemini-3.5-flash")
        opts = server._options_from_start({"type": "start_game", "provider": "anthropic"})
        assert opts.model == server._DEFAULT_MODELS["anthropic"]


class TestServerDefaultsFallback:
    """When the client omits a field, the server's launch defaults apply."""

    def test_provider_falls_back_to_server_default(self) -> None:
        from onuw.web import server
        server.app.state.provider = "anthropic"
        server.app.state.model = server._DEFAULT_MODELS["anthropic"]
        opts = server._options_from_start({"type": "start_game"})
        assert opts.provider == "anthropic"

    def test_summaries_falls_back_to_server_default(self) -> None:
        from onuw.web import server
        server.app.state.summaries = False
        opts = server._options_from_start({"type": "start_game"})
        assert opts.summaries is False

    def test_config_endpoint_reports_server_defaults(self) -> None:
        from onuw.web import server
        server.app.state.provider = "anthropic"
        server.app.state.model = "claude-test"
        server.app.state.summaries = False
        body = asyncio.run(server.config())
        assert body == {
            "provider": "anthropic",
            "model": "claude-test",
            "summaries": False,
        }


class TestSummaries:
    def test_off_emits_none(self) -> None:
        _session, sent = _run_session(summaries=False, caller=_fake_caller)
        assert not any(m["type"] == "round_summary" for m in sent)

    def test_on_emits_one_per_round(self) -> None:
        _session, sent = _run_session(summaries=True, caller=_fake_caller)
        summaries = [m for m in sent if m["type"] == "round_summary"]
        assert len(summaries) == 3
        assert all(m["text"] == "a neutral recap" for m in summaries)
