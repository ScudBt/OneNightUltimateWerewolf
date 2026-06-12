# Phase 2 Spec — Playable CLI Game

> **How to use this:** start Claude Code in the repo root (with `CLAUDE.md`
> present), enter plan mode (`shift+tab`), and paste this spec as your prompt:
> *"Implement Phase 2 per `phase-2-spec.md`. Propose a plan first."*
> Review the plan before approving any edits.

## Goal

Turn the engine + LLM agents into an actually playable CLI game. A human sits
at seat 0, performs their own night action by typing choices, speaks freely
in the discussion, and votes. LLM agents fill the remaining seats. The game
narrates clearly what is happening at each step.

## What changes from Phase 1

| Area | Phase 1 | Phase 2 |
|---|---|---|
| Runnable entry point | `smoke.py` / scripted tests | `cli.py` — interactive game loop |
| Human participation | None | `HumanAgent` at seat 0 |
| Discussion | 1 round, each player speaks once | 3 rounds, each player speaks once per round |
| Player count | 3 (hard-coded in smoke.py) | 4–7, selected at game start |
| Role set | 6 roles | + Minion, Drunk (8 roles total) |
| End-of-game | `GameResult` returned silently | Full reveal: final roles, vote tally, outcome + reason |
| Deck selection | Manual `GameConfig` | Preset configs per player count |

All existing engine tests must continue to pass. The engine itself is not
modified except to support the two new roles.

---

## New roles

### Minion
- **Team:** Werewolf
- **Night ability:** Wakes after Werewolves. Sees who the Werewolves are
  (but Werewolves do NOT see the Minion). Has no night action otherwise.
- **Win condition:** Wins with Werewolves — survives if no Werewolf dies.
  If there are no Werewolves among the players, the Minion wins only if
  at least one player (any player) dies.
- **Night order:** After Werewolf, before Seer.

### Drunk
- **Team:** Village
- **Night ability:** Swaps own card with one center card chosen at random
  (Drunk does not see the new card — they just know they swapped).
- **Win condition:** Village (Drunk's final card determines their actual
  team at resolution, but Drunk never learns what it is during the game).
- **Night order:** After Troublemaker, before Insomniac.
- **Observation emitted:** `DrunkSwapped(center_position: int)` — records
  which center position the swap happened with, but not the role.

Night order in full: Werewolf → Minion → Seer → Robber → Troublemaker → Drunk → Insomniac.

---

## Engine changes

### `roles.py`
Add `MINION` and `DRUNK` to `Role`.

### `observations.py`
Add:
```python
@dataclass(frozen=True)
class SawWerewolvesAsMinion:
    wolf_positions: tuple[int, ...]

@dataclass(frozen=True)
class DrunkSwapped:
    center_position: int
```
Update the `Observation` union.

### `engine.py` — `get_legal_actions`
- `MINION` → `[NoAction()]`
- `DRUNK` → `[DrunkSwapAction(c) for c in center_positions]`
  (Drunk chooses which center slot to swap with — the engine picks randomly
  on behalf of a `RandomAgent` but the human or LLM must choose.)

Wait — re the Drunk's action: the original physical game has the Moderator
swap randomly on the Drunk's behalf (Drunk just points blindly). For the
text version, keep it consistent with other roles: emit one `DrunkSwapAction`
legal action per center slot and let the agent choose. This preserves the
architecture boundary and gives the human a choice (which is actually more
interesting).

### `state.py`
Add `DrunkSwapAction(center_position: int)` to the `Action` union.

### `engine.py` — `apply_night_action`
- **Minion:** Emit `SawWerewolvesAsMinion(tuple(wolf_seats))`. No card swap.
- **Drunk:** Swap `current_roles[seat]` ↔ `current_roles[center_position]`.
  Emit `DrunkSwapped(center_position)`. Do NOT reveal the new role to the Drunk.

### `engine.py` — `evaluate_win`
Update win conditions:

```
Minion present among players?
  → If Minion wins with Werewolves, Minion is added to the wolf-team winners.
  → Special case: no Werewolves among players →
      Minion wins if at least one player dies (Minion "covered" non-existent wolves).
      Village loses in that case (someone died when no wolves existed — unless
      deaths happen to include the Minion, in which it's complex; default: Minion wins
      if alive and at least one death occurred).
```

The exact Minion edge cases are tricky. Implement per the official ONUW rulebook:
- Wolves alive, no wolf dies → wolves + minion win.
- Wolves alive, a wolf dies → village wins (Minion loses).
- No wolves among players, someone dies → Minion wins; village loses.
- No wolves among players, nobody dies → village wins (Minion loses).

### `engine.py` — `run_night`
Extend night order to include Minion and Drunk in the correct positions.

---

## Agent changes

### `agents.py` — `HumanAgent`
```python
class HumanAgent:
    """Reads all decisions from stdin. Never has randomness."""

    def night_action(self, view: PrivateView, legal_actions: list[Action]) -> Action: ...
    def speak(self, view: PrivateView, public_log: list[str]) -> str: ...
    def vote(self, view: PrivateView, public_log: list[str]) -> int: ...
```

Display logic (formatting, printing) lives entirely in `HumanAgent` — the
engine stays silent. For each decision:
- Print a clear prompt showing the legal options numbered.
- Read a line from stdin and parse the integer.
- Re-prompt on invalid input (loop until valid).

`HumanAgent` also has a `show_night_result(view: PrivateView) -> None` method
called by `cli.py` after night to display what the human observed (role dealt,
observations, any swaps).

### `llm_agent.py` — extend for new roles
Add prompting for Minion and Drunk to `LLMAgent`'s role description and
system prompt. Minion knows it's on the wolf team and who the wolves are;
Drunk knows only that it swapped and not what it got.

---

## Discussion — 3 rounds

`run_day` currently does 1 pass (each player speaks once). Extend it to accept
a `rounds: int` parameter (default `3`):

```python
def run_day(state: GameState, agents: dict[int, Agent], rounds: int = 3) -> None:
    for round_num in range(1, rounds + 1):
        state.public_log.append(f"--- Discussion round {round_num} ---")
        for seat in state.player_positions():
            view = build_private_view(state, seat)
            statement = agents[seat].speak(view, list(state.public_log))
            state.public_log.append(f"Player {seat}: {statement}")
```

Each agent sees everything said so far, including prior rounds. The human
types a free-form statement each round; LLM agents reason over the accumulated
log. 3 rounds gives the human time to react to LLM claims and lets LLMs
update their reasoning as the conversation evolves.

---

## Preset deck configurations

Add a `PRESET_CONFIGS` dict in a new `presets.py` (or at the top of `cli.py`):

```python
PRESETS: dict[int, tuple[Role, ...]] = {
    4: (WEREWOLF, WEREWOLF, SEER, ROBBER, TROUBLEMAKER, VILLAGER, VILLAGER),
    5: (WEREWOLF, WEREWOLF, MINION, SEER, ROBBER, TROUBLEMAKER, INSOMNIAC, VILLAGER),
    6: (WEREWOLF, WEREWOLF, MINION, SEER, ROBBER, TROUBLEMAKER, DRUNK, INSOMNIAC, VILLAGER),
    7: (WEREWOLF, WEREWOLF, MINION, SEER, ROBBER, TROUBLEMAKER, DRUNK, INSOMNIAC, VILLAGER, VILLAGER),
}
```

(Exact compositions can be tuned for balance — these are starting points.)
The rule `len(roles) == player_count + 3` must always hold.

---

## `cli.py` — game flow

```
python -m onuw.cli [--seed SEED] [--players N] [--model MODEL]
```

Default: `--players 5`, model from `ANTHROPIC_API_KEY` env.

### Flow

```
1. Print welcome + role list for the chosen player count.
2. Deal (using seed or random seed; print the seed so games are reproducible).
3. Night phase:
   a. Print "Night falls. Close your eyes." 
   b. For each role in night order that exists in the deck:
      - Narrate which role wakes (e.g., "Werewolves, open your eyes.").
      - If it's the human's role: show HumanAgent's night prompt and collect action.
      - Otherwise: LLM agent acts silently (no output).
      - Narrate role going back to sleep.
   c. After all roles: "Everyone opens their eyes. Morning has come."
   d. Show human their private view (role, observations).
4. Discussion phase (3 rounds):
   a. Print round header.
   b. In seat order, print each speaker's name + their statement.
      - For human: prompt for input first, then print.
      - For LLM: print the returned statement.
5. Voting:
   a. Show the public log summary.
   b. Prompt human for vote target.
   c. LLM agents vote (silently collected).
   d. Print all votes simultaneously.
6. Reveal:
   a. Print deaths.
   b. Print each player's FINAL role (what their card actually is after all swaps).
   c. Print the outcome (Village wins / Werewolves win / No winner) with a one-line
      explanation (e.g., "Player 2 was a Werewolf and was eliminated").
```

The CLI owns all printing. The engine never prints.

---

## Tests

Add tests in `tests/test_engine.py` (new section) and a new `tests/test_cli_agents.py`:

- `MINION` night action produces `SawWerewolvesAsMinion` with correct wolf seats.
- `DRUNK` night action swaps the card and emits `DrunkSwapped`.
- Minion win conditions: all four branches (wolves+minion survive; wolf dies;
  no wolves+death; no wolves+no death).
- `run_day` with `rounds=3` appends round headers and 3× the statements.
- `HumanAgent.night_action` with mocked stdin selects the correct `Action`.
- `HumanAgent.vote` with mocked stdin returns the correct seat.

---

## Out of scope for Phase 2 (Phase 3+)

- Voice I/O (speech-to-text for human, text-to-speech for LLM agents).
- Mason role (requires 2 Mason cards and paired-wake logic).
- Hunter role (requires win-condition change: Hunter's vote target dies with them).
- Multi-game session scoring across N games.
- Status UI / visual role-state display.
