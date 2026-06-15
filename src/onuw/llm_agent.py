from __future__ import annotations

import re
from typing import Any, Callable

from onuw.observations import (
    DrunkSwapped,
    InsomniacWoke,
    LoneWolfPeek,
    Observation,
    Robbed,
    SawWerewolves,
    SawWerewolvesAsMinion,
    SeerPeekedCenter,
    SeerPeekedPlayer,
    TroublemakerSwapped,
)
from onuw.roles import Role
from onuw.state import (
    Action,
    DrunkSwapAction,
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
Players: seated 0 to N-1. There are also 3 center cards (slots 0, 1, 2 of the center).
Each player is dealt one role card. Some roles swap cards during the night, so a \
player's final card may differ from their dealt card.

Night order (roles act in this sequence; skip absent roles):
  Werewolf → Minion → Seer → Robber → Troublemaker → Drunk → Insomniac

Night abilities:
  Werewolf    : Sees all other Werewolves. Lone wolf may peek one center card.
  Minion      : Sees who the Werewolves are (wolf team; Werewolves do NOT see the Minion).
  Seer        : Peeks one player's card OR two center cards.
  Robber      : Swaps own card with one other player's card; sees new card.
  Troublemaker: Swaps two OTHER players' cards without seeing them.
  Drunk       : Swaps own card with a chosen center card (does NOT see the new card).
  Insomniac   : After all swaps, wakes and sees own current card.
  Villager    : No night action.

Day phase: All players discuss (3 rounds), then simultaneously vote. The player(s) \
with the most votes die — but only if at least 2 players voted for them. If all \
players are tied at exactly 1 vote each, nobody dies.

Win conditions (decided by FINAL card, not dealt card):
  Village wins    : At least one Werewolf dies, OR no Werewolves among players and \
nobody dies (Minion also loses).
  Werewolves win  : Werewolves alive and no Werewolf dies (Minion also wins).
  Minion special  : If no Werewolves among players, Minion wins only if someone dies.
  No winner       : No Werewolves or Minion among players but someone dies anyway."""

_ROLE_DESCRIPTIONS: dict[Role, str] = {
    Role.WEREWOLF: (
        "You are on the Werewolf team. You win if no Werewolf is killed. "
        "During the night you learn who the other Werewolves are. "
        "If you are the lone Werewolf, you may also peek one center card."
    ),
    Role.MINION: (
        "You are the Minion, on the Werewolf team. "
        "During the night you learn who the Werewolves are, but they do not see you. "
        "You win if no Werewolf is killed. "
        "If there are no Werewolves among the players, you win only if at least one player dies. "
        "Bluff hard to protect the wolves and mislead the village."
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
    Role.DRUNK: (
        "You are the Drunk, on the Village team. "
        "During the night you swapped your card with a center card — "
        "you do NOT know what your card is now. "
        "Your win condition follows your final card, which is unknown to you. "
        "Reason carefully: you might now be a Werewolf without knowing it."
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
    if isinstance(obs, SawWerewolvesAsMinion):
        if obs.wolf_positions:
            wolves = ", ".join(f"Player {p}" for p in obs.wolf_positions)
            return f"As the Minion you see the Werewolves are: {wolves}."
        return "As the Minion you see there are no Werewolves among the players."
    if isinstance(obs, DrunkSwapped):
        label = _pos_label(obs.center_position, player_count)
        return f"You swapped your card with {label}. You don't know your new role."
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
        f"You are Player {view.seat} (dealt role: {view.dealt_role.value.upper()}). "
        f"In the discussion log, lines marked 'You (Player {view.seat})' are your "
        f"OWN earlier statements — do not treat Player {view.seat} as someone else.\n"
        f"Night observations:\n{obs_lines}"
    )


def _serialize_log(public_log: list[str], own_seat: int | None = None) -> str:
    """Render the discussion log. The reader's own lines are relabeled
    ``You (Player N):`` so small models don't treat their own past statements as
    a third party's."""
    if not public_log:
        return "(nothing yet)"
    if own_seat is None:
        return "\n".join(public_log)
    own_prefix = f"Player {own_seat}:"
    out: list[str] = []
    for line in public_log:
        if line.startswith(own_prefix):
            out.append(f"You (Player {own_seat}):{line[len(own_prefix):]}")
        else:
            out.append(line)
    return "\n".join(out)


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


def gemini_caller(
    client: Any,
    model: str = "gemini-2.5-flash-lite",
    thinking_budget: int = 2048,
) -> LLMCaller:
    """Return a caller backed by the Google Gemini API.

    Gemini "thinking" models count hidden reasoning tokens against
    ``max_output_tokens``, so a single cap lets thinking starve the visible
    reply (truncated/empty output). To keep thinking ON without that, we bound
    thinking to ``thinking_budget`` and request ``thinking_budget + max_tokens``
    total — guaranteeing the visible answer always has ``max_tokens`` of room.
    Here ``max_tokens`` from callers is the *visible* output reserve.
    """
    def _call(system: str, user: str, max_tokens: int) -> str:
        response = client.models.generate_content(
            model=model,
            contents=user,
            config={
                "system_instruction": system,
                "max_output_tokens": max_tokens + thinking_budget,
                # dict form (SDK auto-converts) avoids importing google.genai.types
                "thinking_config": {"thinking_budget": thinking_budget},
            },
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
    if isinstance(action, DrunkSwapAction):
        return f"Swap your card with {_pos_label(action.center_position, player_count)} (you won't see your new role)."
    return str(action)  # fallback


def _parse_speak_response(raw: str) -> tuple[str, str]:
    """Extract (reasoning, statement), tolerating truncated/malformed output.

    Critically, this never surfaces the model's private reasoning (or an echoed
    prompt) as the public statement: if the response is cut off before the
    closing ``</statement>`` tag, we still take the open ``<statement>`` content,
    and if there is no usable statement at all we fall back to a neutral line
    rather than dumping the raw text (which would leak reasoning into the log and
    poison every downstream agent that reads it).
    """
    r = re.search(r"<reasoning>(.*?)</reasoning>", raw, re.DOTALL)
    reasoning = r.group(1).strip() if r else ""

    # Accept a <statement> even when its closing tag was truncated away.
    s = re.search(r"<statement>(.*?)(?:</statement>|\Z)", raw, re.DOTALL)
    if s and s.group(1).strip():
        return reasoning, s.group(1).strip()

    # No usable <statement>. If the model emitted any of our format tags, the raw
    # text is reasoning/preamble — do NOT surface it. Only treat the whole
    # response as a plain statement when the model ignored the format entirely.
    if "<reasoning>" in raw or "<statement>" in raw:
        return reasoning, "I'd rather keep my thoughts to myself for now."
    return reasoning, raw.strip()


def _parse_vote_response(raw: str, targets: list[int]) -> tuple[str, int]:
    """Extract (reason, target_seat) from a vote response.

    Tolerant of bare-integer replies: prefers a <vote> tag, then the first
    integer in the text that is a legal target, then falls back to targets[0].
    """
    r = re.search(r"<reason>(.*?)</reason>", raw, re.DOTALL)
    reason = r.group(1).strip() if r else ""

    m = re.search(r"<vote>\s*(\d+)", raw)
    if m and int(m.group(1)) in targets:
        return reason, int(m.group(1))
    for tok in re.findall(r"\d+", raw):
        if int(tok) in targets:
            return reason, int(tok)
    return reason, targets[0]


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
        # seat -> one-line justification the agent gave for its vote
        self.vote_reasoning_log: dict[int, str] = {}

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
            f"Discussion so far:\n{_serialize_log(list(public_log), view.seat)}\n\n"
            f"Make ONE public statement for the group (1-2 sentences). "
            f"You may bluff or tell the truth.\n\n"
            f"Output ONLY the two tags below, with nothing before <reasoning>. "
            f"Keep <reasoning> to 2-3 short sentences and always close both tags:\n"
            f"<reasoning>what you know, what you want others to believe, and why "
            f"you are about to say what you say</reasoning>\n"
            f"<statement>your public statement to the group</statement>"
        )
        raw = self._call(view.dealt_role, prompt, max_tokens=600)
        reasoning, statement = _parse_speak_response(raw)
        self.reasoning_log[view.seat] = reasoning
        return statement

    def vote(self, view: PrivateView, public_log: list[str]) -> int:
        targets = view.legal_vote_targets()
        prompt = (
            f"{_serialize_view(view)}\n\n"
            f"Full discussion log:\n{_serialize_log(list(public_log), view.seat)}\n\n"
            f"Vote to eliminate one player. Legal targets: {', '.join(str(t) for t in targets)}.\n"
            f"Your <reason> is PRIVATE — shown to the human ONLY after the game ends, "
            f"so be fully honest here; do NOT write a public bluff. State your true "
            f"role and team and your real strategic reason for this vote "
            f"(e.g. \"I'm the Minion protecting the wolves, so I voted out a Villager "
            f"alongside my werewolf ally\").\n"
            f"Respond in exactly this format, closing both tags:\n"
            f"<reason>2-3 honest sentences: your true role/team and why you vote this "
            f"player</reason>\n"
            f"<vote>the seat number</vote>"
        )
        raw = self._call(view.dealt_role, prompt, max_tokens=220)
        reason, target = _parse_vote_response(raw, targets)
        self.vote_reasoning_log[view.seat] = reason
        return target
