from __future__ import annotations

import re
from typing import Any, Callable

from onuw.observations import (
    InsomniacWoke,
    LoneWolfPeek,
    Observation,
    Robbed,
    SawWerewolves,
    SeerPeekedCenter,
    SeerPeekedPlayer,
    TroublemakerSwapped,
)
from onuw.roles import Role
from onuw.state import (
    Action,
    LoneWolfPeekAction,
    NoAction,
    PrivateView,
    RobberStealAction,
    SeerPeekCenterAction,
    SeerPeekPlayerAction,
    TroublemakerSwapAction,
)

_GAME_RULES = """\
GAME RULES — One Night Ultimate Werewolf
----------------------------------------
Players: seated 0 to N-1. There are also 3 center cards (positions N, N+1, N+2).
Each player is dealt one role card. Some roles swap cards during the night, so a \
player's final card may differ from their dealt card.

Night order (roles act in this sequence; skip absent roles):
  Werewolf → Seer → Robber → Troublemaker → Insomniac

Night abilities:
  Werewolf  : Sees all other Werewolves. Lone wolf may peek one center card.
  Seer      : Peeks one player's card OR two center cards.
  Robber    : Swaps own card with one other player's card; sees new card.
  Troublemaker: Swaps two OTHER players' cards without seeing them.
  Insomniac : After all swaps, wakes and sees own current card.
  Villager  : No night action.

Day phase: All players discuss, then simultaneously vote. The player(s) with the
most votes die — but only if at least 2 players voted for them. If all players are
tied at exactly 1 vote each, nobody dies.

Win conditions (decided by FINAL card, not dealt card):
  Village wins  : At least one Werewolf dies, OR no Werewolves among players and \
nobody dies.
  Werewolves win: Werewolves are among the players and none of them die.
  No winner     : No Werewolves among players but someone dies anyway."""

_ROLE_DESCRIPTIONS: dict[Role, str] = {
    Role.WEREWOLF: (
        "You are on the Werewolf team. You win if no Werewolf is killed. "
        "During the night you learn who the other Werewolves are. "
        "If you are the lone Werewolf, you may also peek one center card."
    ),
    Role.SEER: (
        "You are on the Village team. You win if at least one Werewolf dies. "
        "During the night you peek either one player's card or two center cards."
    ),
    Role.ROBBER: (
        "You are on the Village team (unless your final card is Werewolf). "
        "During the night you steal another player's card and learn what it is. "
        "Your win condition follows your final card."
    ),
    Role.TROUBLEMAKER: (
        "You are on the Village team. You win if at least one Werewolf dies. "
        "During the night you swap two other players' cards without seeing them."
    ),
    Role.INSOMNIAC: (
        "You are on the Village team (unless your final card is Werewolf). "
        "After all night actions you wake and see your current card. "
        "Your win condition follows your final card."
    ),
    Role.VILLAGER: (
        "You are on the Village team. You win if at least one Werewolf dies. "
        "You have no night action."
    ),
}


def _role_system_prompt(role: Role) -> str:
    return (
        f"{_GAME_RULES}\n\n"
        f"YOUR ROLE THIS GAME\n"
        f"-------------------\n"
        f"{role.value.upper()}: {_ROLE_DESCRIPTIONS[role]}"
    )


def _pos_label(pos: int, player_count: int) -> str:
    return f"Player {pos}" if pos < player_count else f"center slot {pos}"


def _serialize_observation(obs: Observation, player_count: int) -> str:
    if isinstance(obs, SawWerewolves):
        if obs.wolf_positions:
            others = ", ".join(f"Player {p}" for p in obs.wolf_positions)
            return f"You saw that {others} {'is' if len(obs.wolf_positions) == 1 else 'are'} also a Werewolf."
        return "You are the lone Werewolf — no other Werewolves are among the players."
    if isinstance(obs, LoneWolfPeek):
        label = _pos_label(obs.center_position, player_count)
        return f"You peeked at {label} and saw {obs.role.value.upper()}."
    if isinstance(obs, SeerPeekedPlayer):
        return f"You peeked at Player {obs.target} and saw {obs.role.value.upper()}."
    if isinstance(obs, SeerPeekedCenter):
        c1, c2 = obs.targets
        r1, r2 = obs.roles
        l1 = _pos_label(c1, player_count)
        l2 = _pos_label(c2, player_count)
        return f"You peeked at {l1} ({r1.value.upper()}) and {l2} ({r2.value.upper()})."
    if isinstance(obs, Robbed):
        return (
            f"You robbed Player {obs.target} and took their card. "
            f"Your new role is {obs.new_role.value.upper()}."
        )
    if isinstance(obs, TroublemakerSwapped):
        return f"You swapped the cards of Player {obs.target_a} and Player {obs.target_b} (you don't know what they have)."
    if isinstance(obs, InsomniacWoke):
        return f"After all night actions, your card is {obs.final_role.value.upper()}."
    return str(obs)  # fallback; should never reach


def _serialize_view(view: PrivateView) -> str:
    obs_lines = (
        "\n".join(
            f"  - {_serialize_observation(o, view.player_count)}"
            for o in view.observations
        )
        or "  (none)"
    )
    return (
        f"You are Player {view.seat} (dealt role: {view.dealt_role.value.upper()}).\n"
        f"Night observations:\n{obs_lines}"
    )


def _serialize_log(public_log: list[str]) -> str:
    return "\n".join(public_log) if public_log else "(nothing yet)"


# ---------------------------------------------------------------------------
# Provider-agnostic caller type + factory functions
# ---------------------------------------------------------------------------

# (system_prompt, user_prompt, max_tokens) -> response_text
LLMCaller = Callable[[str, str, int], str]


def anthropic_caller(client: Any, model: str = "claude-sonnet-4-6") -> LLMCaller:
    """Return a caller backed by the Anthropic messages API with prompt caching."""
    def _call(system: str, user: str, max_tokens: int) -> str:
        response = client.messages.create(
            model=model,
            max_tokens=max_tokens,
            system=[{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}],
            messages=[{"role": "user", "content": user}],
        )
        return str(response.content[0].text).strip()
    return _call


def gemini_caller(client: Any, model: str = "gemini-2.5-flash-lite") -> LLMCaller:
    """Return a caller backed by the Google Gemini API."""
    def _call(system: str, user: str, max_tokens: int) -> str:
        response = client.models.generate_content(
            model=model,
            contents=user,
            config={"system_instruction": system, "max_output_tokens": max_tokens},
        )
        return (response.text or "").strip()
    return _call


def _serialize_action(action: Action, player_count: int) -> str:
    if isinstance(action, NoAction):
        return "Take no action."
    if isinstance(action, LoneWolfPeekAction):
        return f"Peek at {_pos_label(action.center_position, player_count)}."
    if isinstance(action, SeerPeekPlayerAction):
        return f"Peek at Player {action.target}'s card."
    if isinstance(action, SeerPeekCenterAction):
        c1, c2 = action.targets
        return f"Peek at {_pos_label(c1, player_count)} and {_pos_label(c2, player_count)}."
    if isinstance(action, RobberStealAction):
        return f"Rob Player {action.target} (swap your card with theirs and learn your new card)."
    if isinstance(action, TroublemakerSwapAction):
        return f"Swap the cards of Player {action.target_a} and Player {action.target_b}."
    return str(action)  # fallback


def _parse_speak_response(raw: str) -> tuple[str, str]:
    """Extract (reasoning, statement) from a <reasoning>…</reasoning><statement>…</statement> response."""
    r = re.search(r"<reasoning>(.*?)</reasoning>", raw, re.DOTALL)
    s = re.search(r"<statement>(.*?)</statement>", raw, re.DOTALL)
    reasoning = r.group(1).strip() if r else ""
    statement = s.group(1).strip() if s else raw.strip()
    return reasoning, statement


class LLMAgent:
    """Agent that delegates decisions to any LLM via a provider-agnostic caller.

    Construct with one of the factory functions:
        LLMAgent(anthropic_caller(client))
        LLMAgent(gemini_caller(client))

    After the day phase, ``reasoning_log`` maps each seat to the private
    reasoning that agent produced before speaking.
    """

    def __init__(self, caller: LLMCaller) -> None:
        self._caller = caller
        self.reasoning_log: dict[int, str] = {}

    def _call(self, role: Role, user_prompt: str, max_tokens: int = 200) -> str:
        return self._caller(_role_system_prompt(role), user_prompt, max_tokens).strip()

    def night_action(self, view: PrivateView, legal_actions: list[Action]) -> Action:
        if len(legal_actions) == 1:
            return legal_actions[0]

        numbered = "\n".join(
            f"{i + 1}. {_serialize_action(a, view.player_count)}"
            for i, a in enumerate(legal_actions)
        )
        prompt = (
            f"{_serialize_view(view)}\n\n"
            f"Choose your night action by responding with just the option number:\n"
            f"{numbered}"
        )
        raw = self._call(view.dealt_role, prompt, max_tokens=10)
        try:
            idx = int(raw.split()[0]) - 1
            if 0 <= idx < len(legal_actions):
                return legal_actions[idx]
        except (ValueError, IndexError):
            pass
        return legal_actions[0]

    def speak(self, view: PrivateView, public_log: list[str]) -> str:
        prompt = (
            f"{_serialize_view(view)}\n\n"
            f"Discussion so far:\n{_serialize_log(list(public_log))}\n\n"
            f"Make ONE public statement for the group (1-2 sentences). "
            f"You may bluff or tell the truth.\n\n"
            f"Respond in exactly this format:\n"
            f"<reasoning>2-3 sentences: what you know, what you want others to "
            f"believe, and why you are saying what you are about to say</reasoning>\n"
            f"<statement>your public statement to the group</statement>"
        )
        raw = self._call(view.dealt_role, prompt, max_tokens=400)
        reasoning, statement = _parse_speak_response(raw)
        self.reasoning_log[view.seat] = reasoning
        return statement

    def vote(self, view: PrivateView, public_log: list[str]) -> int:
        targets = view.legal_vote_targets()
        prompt = (
            f"{_serialize_view(view)}\n\n"
            f"Full discussion log:\n{_serialize_log(list(public_log))}\n\n"
            f"Vote to eliminate one player. Legal targets: {', '.join(str(t) for t in targets)}.\n"
            f"Respond with just the seat number."
        )
        raw = self._call(view.dealt_role, prompt, max_tokens=10)
        try:
            target = int(raw.split()[0])
            if target in targets:
                return target
        except (ValueError, IndexError):
            pass
        return targets[0]
