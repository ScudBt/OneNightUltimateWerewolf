# Phase 1 Spec — LLM Agents + Day Discussion

> **How to use this:** start Claude Code in the repo root (with `CLAUDE.md`
> present), enter plan mode (`shift+tab`), and paste this spec as your prompt:
> *"Implement Phase 1 per `phase-1-spec.md`. Propose a plan first."*
> Review the plan before approving any edits.

## Goal

Replace the scripted/random reference agents with real LLM agents that reason
from private information, bluff or tell the truth during a discussion round, and
vote based on what was said. The engine's hard boundary (agents never see
`GameState`) must remain intact. All existing engine tests must continue to pass.

## What changed from Phase 0

| Area | Phase 0 | Phase 1 |
|---|---|---|
| Day phase | Stub (`"[day phase stub]"` appended once) | Real discussion round: each player speaks once in seat order |
| Agent protocol | `night_action` + `vote` | + `speak` |
| Agent implementations | `RandomAgent`, `ScriptedAgent` | + `LLMAgent` |
| Dependencies | stdlib + pytest | + `anthropic`, `google-genai` |

## Agent protocol extension

Add one method to the `Agent` protocol (all existing implementations must add it):

```python
def speak(self, view: PrivateView, public_log: list[str]) -> str:
    ...
```

- Called once per player, in seat order, before voting.
- Receives the same `PrivateView` as the other methods (private info + log so far).
- Returns a plain string — no validation; engine appends it verbatim.
- `RandomAgent.speak` → deterministic fixed string (`"I have nothing to add."`).
- `ScriptedAgent.speak` → replay from an optional `statements: list[str]` param;
  return `"Pass."` when the list is exhausted.

## Day phase (`run_day`)

Add `run_day(state: GameState, agents: dict[int, Agent]) -> None` to `engine.py`:

```python
def run_day(state, agents):
    for seat in state.player_positions():
        view = build_private_view(state, seat)
        statement = agents[seat].speak(view, list(state.public_log))
        state.public_log.append(f"Player {seat}: {statement}")
```

Each agent sees the accumulated log up to its turn, so later speakers can
react to what earlier speakers said. Replace the Phase 0 stub in `game.py`
with a call to `run_day(state, agents)`.

## LLM agent

### Provider abstraction

`LLMAgent` is **provider-agnostic**. It accepts a `LLMCaller`:

```python
LLMCaller = Callable[[str, str, int], str]
# (system_prompt, user_prompt, max_tokens) -> response_text
```

Two factory functions produce callers:

- `anthropic_caller(client, model="claude-sonnet-4-6")` — uses
  `client.messages.create` with `cache_control: ephemeral` on the system prompt.
- `gemini_caller(client, model="gemini-2.5-flash-lite")` — uses
  `client.models.generate_content` with `system_instruction`.

Construct an agent as `LLMAgent(anthropic_caller(client))` or
`LLMAgent(gemini_caller(client))`. Adding a new provider requires only a new
factory function; `LLMAgent` itself does not change.

### Prompt structure (all three methods)

Every call follows the same two-part structure:

**System message** (passed to the factory, cached by `anthropic_caller`):
```
[Game rules: roles, night order, vote rules, win conditions]

YOUR ROLE THIS GAME
-------------------
<ROLE_NAME>: <one-line description of night ability and win condition>
```

**User message** (dynamic per call):

| Method | Contents |
|---|---|
| `night_action` | Serialized view + numbered list of legal actions; ask for the number |
| `speak` | Serialized view + discussion log so far; ask for one 1-2 sentence statement |
| `vote` | Serialized view + full discussion log + comma-separated legal targets; ask for the seat number |

### Parsing and fallback

- `night_action`: parse first token as 1-based index into `legal_actions`.
  Fallback to `legal_actions[0]` on bad parse or out-of-range.
- `vote`: parse first token as seat integer, validate against
  `view.legal_vote_targets()`. Fallback to `targets[0]`.
- `speak`: return stripped response text verbatim.
- When only one legal night action exists, skip the API call entirely and
  return it directly.

### Serialization helpers (module-level pure functions)

- `_role_system_prompt(role)` — combines game rules + role description.
- `_serialize_view(view)` — seat, dealt role, observations as readable English.
- `_serialize_observation(obs, player_count)` — human-readable per observation type.
- `_serialize_action(action, player_count)` — human-readable per action type.
- `_serialize_log(public_log)` — join lines or return `"(nothing yet)"`.
- `_pos_label(pos, player_count)` — `"Player N"` or `"center slot N"`.

## Smoke script (`smoke.py`)

A standalone script at the repo root for manual end-to-end runs. Not part of
the package; not covered by the test suite.

```
uv run --env-file .env python smoke.py [--seed N] [--players 3|4] [--model ID]
```

Reads `GEMINI_API_KEY` (or `GOOGLE_API_KEY`) from the environment / `.env` file.
Defaults: seed=42, players=3, model=`gemini-2.5-flash-lite`.

Prints in order: **DEAL** (ground truth) → **NIGHT OBSERVATIONS** (per player) →
**DAY DISCUSSION** (from public log) → **VOTE** (each player's target) →
**FINAL CARD ASSIGNMENTS** (with change markers) → **RESULT**.

## Testing

All tests in `tests/test_llm_agent.py` use a typed fake caller (a closure over
a `list[str]` of scripted responses) — no real API calls, no API key required.

Coverage required:

- `night_action`: single legal action skips API; numbered choice picks correctly;
  bad-parse and out-of-range fall back to index 0.
- `speak`: response is stripped and returned verbatim.
- `vote`: valid seat returned; own seat or bad parse falls back to first legal target.
- `run_day`: public log contains one `"Player N: ..."` entry per seat in seat order;
  each subsequent agent receives the prior entries in its log.
- `RandomAgent.speak`: returns a non-empty string.
- `ScriptedAgent.speak`: replays statements in order; returns `"Pass."` when
  exhausted; works with no statements supplied.
- Full `play_game` with mocked LLM agents completes without error.

Run with `pytest -q`. All 60 tests (39 engine + 21 LLM-agent) must pass.

## Dependencies added

```
anthropic>=0.40
google-genai
```

Both are runtime dependencies in `pyproject.toml` (managed via `uv add`).

## Out of scope for Phase 1

Voice I/O, status UI, multi-round discussion, agent memory across games,
evaluation harness, prompt tuning, and any new roles. Do not add them.
