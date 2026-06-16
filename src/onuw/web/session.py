"""Async game driver for one browser session.

``GameSession`` mirrors the phase flow of ``cli.run_game`` but emits protocol
events over a callback instead of printing, and awaits the human's choices from
an inbound queue instead of reading stdin. It reuses the engine's pure functions
verbatim — no ground-truth logic is duplicated here.

The blocking ``LLMAgent`` methods run via ``asyncio.to_thread`` so streaming
output and human input stay responsive while an agent "thinks".
"""
from __future__ import annotations

import asyncio
import random
from collections import Counter
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Optional, Protocol, TextIO

from onuw.agents import Agent, _describe_action, _describe_observation
from onuw.cli import _ROLE_INTROS
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
from onuw.state import Action, GameConfig, GameResult, GameState, PrivateView
from onuw.web import protocol
from onuw.web.personas import assign_personas

TOTAL_ROUNDS = 3

Send = Callable[[dict[str, Any]], Awaitable[None]]


class ClientGone(Exception):
    """Raised when the browser disconnects mid-game so ``run`` can abort."""


@dataclass(frozen=True)
class GameOptions:
    player_count: int
    seed: int
    provider: str = "gemini"
    model: str = "gemini-2.5-flash-lite"
    summaries: bool = True


# ---------------------------------------------------------------------------
# Caller factory (raises on failure; the server turns this into an error event)
# ---------------------------------------------------------------------------

def make_caller(provider: str, model: str) -> LLMCaller:
    if provider == "anthropic":
        import anthropic

        return anthropic_caller(anthropic.Anthropic(), model)
    from google import genai

    return gemini_caller(genai.Client(), model)


def _role_counts(roles: tuple[Role, ...]) -> list[dict[str, Any]]:
    """Public deck composition with counts, e.g. [{"role": "werewolf", "count": 2}]."""
    counts = Counter(r.value for r in roles)
    return [{"role": role, "count": counts[role]} for role in sorted(counts)]


# ---------------------------------------------------------------------------
# Human agent backed by the browser (async — does not satisfy the sync Agent
# protocol; the session calls it directly for the human seat).
# ---------------------------------------------------------------------------

class HumanInterface(Protocol):
    async def night_action(self, view: PrivateView, legal: list[Action]) -> Action: ...
    async def speak(self, view: PrivateView, public_log: list[str]) -> str: ...
    async def vote(self, view: PrivateView, public_log: list[str]) -> int: ...
    async def react(self) -> str: ...


class WebHumanAgent:
    """Awaits the human's decisions from the inbound message queue."""

    def __init__(self, send: Send, inbound: "asyncio.Queue[dict[str, Any]]") -> None:
        self._send = send
        self._inbound = inbound
        # The optional free-text reason from the human's most recent vote.
        self.last_vote_reason: str = ""

    async def _next(self) -> dict[str, Any]:
        msg = await self._inbound.get()
        if msg.get("type") == "__disconnect__":
            raise ClientGone()
        return msg

    async def night_action(self, view: PrivateView, legal: list[Action]) -> Action:
        if len(legal) == 1:
            return legal[0]
        await self._send(
            protocol.night_prompt(
                [_describe_action(a, view.player_count) for a in legal]
            )
        )
        while True:
            msg = await self._next()
            if msg.get("type") == "night_choice":
                idx = msg.get("index")
                if isinstance(idx, int) and 0 <= idx < len(legal):
                    return legal[idx]
            await self._send(protocol.invalid_input("Please pick one of the listed options."))

    async def speak(self, view: PrivateView, public_log: list[str]) -> str:
        while True:
            msg = await self._next()
            if msg.get("type") == "statement":
                text = str(msg.get("text", "")).strip()
                return text or "Pass."
            await self._send(protocol.invalid_input("Type a statement, then send."))

    async def vote(self, view: PrivateView, public_log: list[str]) -> int:
        targets = view.legal_vote_targets()
        await self._send(protocol.vote_prompt(targets))
        while True:
            msg = await self._next()
            if msg.get("type") == "vote":
                target = msg.get("seat")
                if isinstance(target, int) and target in targets:
                    self.last_vote_reason = str(msg.get("reason", "")).strip()
                    return target
            await self._send(protocol.invalid_input("Pick a valid player to eliminate."))

    async def react(self) -> str:
        """Await the human's optional one-line reaction on the reveal screen."""
        while True:
            msg = await self._next()
            if msg.get("type") == "reaction":
                return str(msg.get("text", "")).strip()


# ---------------------------------------------------------------------------
# Session driver
# ---------------------------------------------------------------------------

class GameSession:
    def __init__(
        self,
        send: Send,
        inbound: "asyncio.Queue[dict[str, Any]]",
        options: GameOptions,
        *,
        caller: Optional[LLMCaller] = None,
        agent_factory: Optional[Callable[[int, Role], Agent]] = None,
        human_agent: Optional[HumanInterface] = None,
        log_file: Optional[TextIO] = None,
    ) -> None:
        self._send = send
        self._inbound = inbound
        self._opt = options
        self._caller = caller
        self._agent_factory = agent_factory
        self._human_override = human_agent
        self._log_file = log_file

        # Populated during run() for inspection / testing.
        self.state: Optional[GameState] = None
        self.result: Optional[GameResult] = None
        self.human_seat: int = -1

    # -- helpers ----------------------------------------------------------

    def _ensure_caller(self) -> LLMCaller:
        if self._caller is None:
            self._caller = make_caller(self._opt.provider, self._opt.model)
        return self._caller

    def _build_agent(self, seat: int, role: Role) -> Agent:
        if self._agent_factory is not None:
            return self._agent_factory(seat, role)
        return LLMAgent(self._ensure_caller())

    def _log(self, line: str = "") -> None:
        """Write a line to the run transcript (no-op without a log file)."""
        if self._log_file is None:
            return
        self._log_file.write(line + "\n")
        self._log_file.flush()

    def _log_reasoning(self, agent: Agent, seat: int) -> None:
        if isinstance(agent, LLMAgent):
            reasoning = agent.reasoning_log.get(seat, "")
            if reasoning:
                self._log(f"    [private reasoning] {reasoning}")

    async def _summarize(
        self,
        round_num: int,
        lines: list[tuple[int, str]],
        personas: dict[int, tuple[str, str]],
    ) -> str:
        if not lines or self._caller is None:
            return ""
        transcript = "\n".join(f"{personas[s][0]}: {t}" for s, t in lines)
        system = (
            "You are a neutral narrator for a social-deduction game. Summarize the "
            "round's PUBLIC discussion in ONE short sentence. Use only what was said; "
            "never invent hidden roles or motives."
        )
        user = f"Round {round_num} statements:\n{transcript}\n\nOne-sentence neutral recap:"
        caller = self._caller
        # 120 = visible reserve; the caller adds any thinking budget on top.
        return (await asyncio.to_thread(caller, system, user, 120)).strip()

    def _board_lines(
        self,
        state: GameState,
        personas: dict[int, tuple[str, str]],
        human_seat: int,
    ) -> str:
        """Omniscient dealt->final board, flagging every night swap. Post-game only."""
        lines = []
        for s in range(state.player_count):
            dealt = state.dealt_roles[s].value.upper()
            final = state.current_roles[s].value.upper()
            you = " (the human)" if s == human_seat else ""
            if final != dealt:
                lines.append(
                    f"P{s} {personas[s][0]}{you}: dealt {dealt}, "
                    f"but ended as {final} after the night swaps"
                )
            else:
                lines.append(f"P{s} {personas[s][0]}{you}: {final} (never swapped)")
        return "\n".join(lines)

    async def _god_summary(
        self,
        state: GameState,
        personas: dict[int, tuple[str, str]],
        votes: dict[int, int],
        result: GameResult,
        human_seat: int,
    ) -> str:
        """Omniscient narration of what really happened across the whole game."""
        if self._caller is None:
            return ""
        transcript = "\n".join(
            line for line in state.public_log if not line.startswith("--- Round")
        )
        vote_text = "\n".join(
            f"P{v} {personas[v][0]} voted for P{votes[v]} ({personas[votes[v]][0]})"
            for v in sorted(votes)
        )
        winners = ", ".join(f"P{w}" for w in sorted(result.winners)) or "nobody"
        system = (
            "You are the omniscient game master of a One Night Ultimate Werewolf "
            "game. You alone know every player's true dealt and final card and what "
            "each player privately knew. Narrate what REALLY happened in 3-5 short "
            "sentences: who bluffed a role they never held, who was confounded by a "
            "night swap they never realized, who over-claimed or contradicted "
            "themselves and got busted, and how that shaped the vote. Refer to "
            "players by name. Be concrete and a little dramatic; never invent facts "
            "outside the ground truth given."
        )
        user = (
            f"GROUND TRUTH (cards):\n{self._board_lines(state, personas, human_seat)}\n\n"
            f"PUBLIC DISCUSSION:\n{transcript}\n\n"
            f"VOTES:\n{vote_text}\n\n"
            f"OUTCOME: {result.outcome.value}. Winners: {winners}.\n\n"
            f"Now narrate what really happened:"
        )
        caller = self._caller
        return (await asyncio.to_thread(caller, system, user, 280)).strip()

    async def _npc_reactions(
        self,
        state: GameState,
        personas: dict[int, tuple[str, str]],
        votes: dict[int, int],
        result: GameResult,
        human_seat: int,
    ) -> dict[int, str]:
        """One short in-character reaction per NPC, now that the truth is out."""
        if self._caller is None:
            return {}
        caller = self._caller
        board = self._board_lines(state, personas, human_seat)
        seats = [s for s in state.player_positions() if s != human_seat]

        async def _react(s: int) -> str:
            won = s in result.winners
            system = (
                f"You are {personas[s][0]}, a player in One Night Ultimate Werewolf. "
                f"Your card ended as {state.current_roles[s].value.upper()} "
                f"(dealt {state.dealt_roles[s].value.upper()}). The game is over and "
                f"all cards are now revealed to you. React to the result in ONE short, "
                f"emotional first-person line (max 15 words), e.g. \"Knew it!\" or "
                f"\"Robbed — should've trusted Mona.\" Stay in character; no analysis."
            )
            user = (
                f"Final board (now revealed):\n{board}\n\n"
                f"You voted for P{votes[s]} ({personas[votes[s]][0]}). "
                f"Your team {'WON' if won else 'LOST'}.\n\nYour one-line reaction:"
            )
            return (await asyncio.to_thread(caller, system, user, 60)).strip()

        # Independent per-NPC calls; fire them concurrently.
        texts = await asyncio.gather(*(_react(s) for s in seats))
        return dict(zip(seats, texts))

    # -- main loop --------------------------------------------------------

    async def run(self) -> None:
        opt = self._opt
        roles = PRESETS[opt.player_count]
        config = GameConfig(player_count=opt.player_count, roles=roles, seed=opt.seed)
        rng = random.Random(opt.seed)
        human_seat = rng.randint(0, opt.player_count - 1)
        self.human_seat = human_seat
        state = deal(config, rng)
        self.state = state

        personas = assign_personas(opt.seed, opt.player_count, human_seat)

        human: HumanInterface = self._human_override or WebHumanAgent(
            self._send, self._inbound
        )

        roster = [
            {
                "seat": s,
                "name": personas[s][0],
                "avatar": personas[s][1],
                "is_human": s == human_seat,
            }
            for s in range(opt.player_count)
        ]
        await self._send(
            protocol.game_start(
                seed=opt.seed,
                player_count=opt.player_count,
                provider=opt.provider,
                model=opt.model,
                human_seat=human_seat,
                roster=roster,
                rounds=TOTAL_ROUNDS,
                roles_in_deck=_role_counts(roles),
            )
        )
        human_role = state.dealt_roles[human_seat]
        await self._send(protocol.your_role(human_role, _ROLE_INTROS[human_role]))

        # Full run transcript (parity with the CLI's tee'd log).
        self._log("=" * 60)
        self._log("ONE NIGHT ULTIMATE WEREWOLF (web)")
        self._log("=" * 60)
        self._log(
            f"Seed: {opt.seed} | Players: {opt.player_count} | "
            f"Provider: {opt.provider} ({opt.model})"
        )
        self._log(f"Roles in deck: {', '.join(sorted(r.value for r in roles))}")
        self._log(
            f"Human: Player {human_seat} ({personas[human_seat][0]}) — "
            f"role {human_role.value.upper()}"
        )
        self._log("Seating: " + ", ".join(
            f"P{s}={personas[s][0]}" for s in range(opt.player_count)
        ))

        # Build the NPC agents only after the board has been sent, so a caller
        # failure (e.g. missing API key) surfaces with the game already on screen.
        agents: dict[int, Agent] = {}
        for seat in range(opt.player_count):
            if seat != human_seat:
                agents[seat] = self._build_agent(seat, state.dealt_roles[seat])

        await self._run_night(state, agents, human, human_seat, personas)
        await self._run_discussion(state, agents, human, human_seat, personas)
        await self._run_vote_and_reveal(state, agents, human, human_seat, personas)

    async def _run_night(
        self,
        state: GameState,
        agents: dict[int, Agent],
        human: HumanInterface,
        human_seat: int,
        personas: dict[int, tuple[str, str]],
    ) -> None:
        self._log("\n--- NIGHT ---")
        # The narrator calls every role present in the deck (player + center
        # cards), regardless of whether any player holds it. Suppressing the
        # call for a role whose copies are all in the center would leak hidden
        # card locations — e.g. an omitted Werewolf wake proves no player is a
        # Werewolf. Only the action loop is gated on a role actually being held.
        deck_roles = set(state.dealt_roles.values())
        for role in WAKE_ORDER:
            if role not in deck_roles:
                continue
            seats = [p for p in state.player_positions() if state.dealt_roles[p] == role]
            await self._send(protocol.night_wake(role))
            self._log(f"[Night] The {role.value.upper()} wakes.")
            for seat in seats:
                legal = get_legal_actions(state, seat)
                view = build_private_view(state, seat)
                if seat == human_seat:
                    action = await human.night_action(view, legal)
                    if len(legal) > 1:
                        self._log(
                            f"  You (P{seat}) chose: "
                            f"{_describe_action(action, state.player_count)}"
                        )
                else:
                    action = await asyncio.to_thread(
                        agents[seat].night_action, view, legal
                    )
                if action not in legal:
                    raise ValueError(
                        f"Seat {seat} returned illegal night action {action!r}"
                    )
                apply_night_action(state, seat, action)
            await self._send(protocol.night_sleep(role))

        view = build_private_view(state, human_seat)
        await self._send(
            protocol.night_result(
                state.dealt_roles[human_seat], view.observations, state.player_count
            )
        )
        self._log(f"\nYour night observations (P{human_seat}):")
        if view.observations:
            for obs in view.observations:
                self._log(f"  - {_describe_observation(obs, state.player_count)}")
        else:
            self._log("  (none)")

    async def _run_discussion(
        self,
        state: GameState,
        agents: dict[int, Agent],
        human: HumanInterface,
        human_seat: int,
        personas: dict[int, tuple[str, str]],
    ) -> None:
        for round_num in range(1, TOTAL_ROUNDS + 1):
            await self._send(protocol.round_start(round_num, TOTAL_ROUNDS))
            state.public_log.append(f"--- Round {round_num} ---")
            self._log(f"\n--- DISCUSSION Round {round_num} ---")
            round_lines: list[tuple[int, str]] = []
            for seat in state.player_positions():
                await self._send(protocol.speaker_thinking(seat, personas[seat][0]))
                view = build_private_view(state, seat)
                if seat == human_seat:
                    statement = await human.speak(view, list(state.public_log))
                else:
                    statement = await asyncio.to_thread(
                        agents[seat].speak, view, list(state.public_log)
                    )
                state.public_log.append(f"Player {seat}: {statement}")
                round_lines.append((seat, statement))
                you = " (you)" if seat == human_seat else ""
                self._log(f"P{seat} {personas[seat][0]}{you}: {statement}")
                if seat != human_seat:
                    self._log_reasoning(agents[seat], seat)
                await self._send(
                    protocol.statement(seat, personas[seat][0], round_num, statement)
                )
            if self._opt.summaries:
                summary = await self._summarize(round_num, round_lines, personas)
                if summary:
                    self._log(f"[Round {round_num} summary] {summary}")
                    await self._send(protocol.round_summary(round_num, summary))

    async def _run_vote_and_reveal(
        self,
        state: GameState,
        agents: dict[int, Agent],
        human: HumanInterface,
        human_seat: int,
        personas: dict[int, tuple[str, str]],
    ) -> None:
        votes: dict[int, int] = {}
        vote_reasons: dict[int, str] = {}

        # NPC votes are independent of one another (none is appended to the
        # public log during collection), so we fire them concurrently instead
        # of one round-trip at a time. They are also kicked off before awaiting
        # the human's vote, so they overlap with the human's thinking time.
        npc_seats = [s for s in state.player_positions() if s != human_seat]

        async def _npc_vote(seat: int) -> int:
            view = build_private_view(state, seat)
            target = await asyncio.to_thread(
                agents[seat].vote, view, list(state.public_log)
            )
            if target not in view.legal_vote_targets():
                raise ValueError(
                    f"Seat {seat} cast illegal vote for {target!r}"
                )
            return target

        npc_task = asyncio.gather(*(_npc_vote(s) for s in npc_seats))

        human_view = build_private_view(state, human_seat)
        votes[human_seat] = await human.vote(human_view, list(state.public_log))
        vote_reasons[human_seat] = getattr(human, "last_vote_reason", "")

        npc_votes = await npc_task
        for seat, vote in zip(npc_seats, npc_votes):
            votes[seat] = vote
            agent = agents[seat]
            if isinstance(agent, LLMAgent):
                vote_reasons[seat] = agent.vote_reasoning_log.get(seat, "")
        await self._send(protocol.votes_revealed(votes))
        self._log("\n--- VOTES ---")
        for voter in sorted(votes):
            you = " (you)" if voter == human_seat else ""
            reason = vote_reasons.get(voter, "")
            tail = f"  — {reason}" if reason else ""
            self._log(
                f"P{voter} {personas[voter][0]}{you}"
                f" -> P{votes[voter]} ({personas[votes[voter]][0]}){tail}"
            )

        deaths = resolve_vote(votes)
        result = evaluate_win(state, deaths)
        self.result = result
        # Send the reveal screen the instant the (deterministic) result is known,
        # without waiting on the NPC-reaction and god-view LLM calls. Reactions
        # are streamed in afterward via a separate ``reactions`` message.
        await self._send(
            protocol.reveal(
                player_count=state.player_count,
                dealt=state.dealt_roles,
                final=state.current_roles,
                result=result,
                human_seat=human_seat,
                personas=personas,
                deaths=deaths,
                votes=votes,
                vote_reasons=vote_reasons,
                reactions={},
            )
        )
        self._log("\n--- REVEAL ---")
        if deaths:
            self._log("Eliminated: " + ", ".join(
                f"P{d} ({personas[d][0]})" for d in sorted(deaths)
            ))
        else:
            self._log("Nobody was eliminated.")
        self._log("Final cards:")
        for s in range(state.player_count):
            dealt = state.dealt_roles[s].value.upper()
            final = state.current_roles[s].value.upper()
            note = f"  [dealt {dealt}]" if final != dealt else ""
            you = " (you)" if s == human_seat else ""
            self._log(f"  P{s} {personas[s][0]}{you}: {final}{note}")
        self._log(f"Outcome: {result.outcome.value}")
        self._log("Winners: " + (
            ", ".join(f"P{w}" for w in sorted(result.winners)) or "(none)"
        ))
        self._log("Result for you: " + ("WON" if human_seat in result.winners else "LOST"))

        # Reactions and god-view are independent post-game LLM calls; run them
        # concurrently and stream each in as it lands.
        reactions, god = await asyncio.gather(
            self._npc_reactions(state, personas, votes, result, human_seat),
            self._god_summary(state, personas, votes, result, human_seat),
        )
        if reactions:
            await self._send(protocol.reactions(reactions))
            self._log("\n--- REACTIONS ---")
            for s in sorted(reactions):
                self._log(f"  P{s} {personas[s][0]}: {reactions[s]}")

        if god:
            await self._send(protocol.god_summary(god))
            self._log("\n--- GOD VIEW ---")
            self._log(god)

        # Optional closing reaction from the human, collected on the reveal screen.
        reaction = await human.react()
        if reaction:
            await self._send(protocol.human_reaction(human_seat, reaction))
            self._log(f"\nYour reaction (P{human_seat}): {reaction}")
