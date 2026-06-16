# Phase 3 Spec — Web UI

> **How to use this:** start Claude Code in the repo root (with `CLAUDE.md`
> present), enter plan mode (`shift+tab`), and paste this spec as your prompt:
> *"Implement Phase 3 per `phase-3-spec.md`. Propose a plan first."*
> Review the plan before approving any edits.

## Goal

Give the playable game a **browser UI** that is friendly and fun to play. Today
the human plays through [cli.py](src/onuw/cli.py): a linear wall of stdout where
the player's secret role scrolls away, every agent speaks in flat seat order,
and there is no sense of place. Phase 3 replaces that surface with a single-page
web app where the human always sees their role and observations, watches named
opponents "type" their statements live, tracks the discussion per-player, and
gets an illustrated, atmospheric reveal at the end.

**The engine does not change.** Phase 3 is a presentation + orchestration layer
on top of the existing engine. All night-action logic, swaps, vote resolution,
and win evaluation stay exactly as they are in [engine.py](src/onuw/engine.py).
The existing CLI stays runnable and untouched.

## Decisions locked for this phase

| Decision | Choice |
|---|---|
| Medium | Web app in the browser |
| Backend | FastAPI + `uvicorn`, one WebSocket per game |
| Frontend | Plain HTML/CSS/JS, no build step, no framework |
| Voice | **Out of scope** — deferred to Phase 4 (visual only this phase) |
| Discussion feel | Streamed "typing" effect as statements appear |
| Opponents | Named characters + avatars (cosmetic only; never leak role) |
| Art | Illustrated role art (one image per role, with a CSS fallback) |
| Always-visible | Your role + observations · player roster · phase/round banner · per-player transcript |
| Transcript | Grouped **under each player**, not one giant thread |
| Round summaries | Nice-to-have; behind a flag, easy to cut (see below) |

---

## The hard boundary still holds — now over the network

The engine boundary in `CLAUDE.md` becomes a **network boundary** in Phase 3:

- The browser is "an agent." It receives only the human's **`PrivateView`** plus
  public events — exactly what an `Agent` is allowed to see.
- The server **never** sends to the browser: any other player's dealt/current
  role, the center cards, the full `GameState`, or any LLM agent's private
  `reasoning_log`. The reasoning that `LLMAgent.speak` parses out of
  `<reasoning>…</reasoning>` stays server-side (log file only), exactly as today.
- Player **names** are a display skin assigned independently of role, so they
  carry no information. Roles are revealed to the browser **only** in the final
  reveal payload, after `evaluate_win` has run.

A good mental test for every WebSocket message: *would it be legal to hand this
to an `Agent`'s `speak()`?* If not, it must not cross the wire.

---

## Tech stack & dependencies

Add to the project (kept minimal):

- `fastapi`, `uvicorn[standard]` — server + WebSocket + static file serving.
- No frontend dependencies. `web/static/` is served as-is.

Python 3.11+, type-annotated, `mypy --strict`-clean like the rest of the repo.
The engine remains pure-stdlib; the new web package is the only place importing
FastAPI.

---

## Module layout

New package `src/onuw/web/` (the engine package is not touched except where
noted):

```
src/onuw/web/
  __init__.py
  __main__.py        # `python -m onuw.web` entry point (argparse like cli.py)
  server.py          # FastAPI app, routes, WebSocket endpoint
  session.py         # GameSession: async driver of one game over a WebSocket
  protocol.py        # typed server→client / client→server message schemas
  personas.py        # seat → (name, avatar) cosmetic mapping
  static/
    index.html
    app.js
    styles.css
    assets/roles/    # werewolf.png, seer.png, ... (+ fallback handling)
```

`cli.py`, `game.py`, and `engine.py` orchestration helpers stay as they are. The
web `GameSession` drives the loop itself (it needs to interleave streaming and
awaiting human input), reusing the engine's pure functions: `deal`,
`get_legal_actions`, `build_private_view`, `apply_night_action`, `resolve_vote`,
`evaluate_win`, and the `WAKE_ORDER` constant.

---

## Server design

### `__main__.py`

```
python -m onuw.web [--host 127.0.0.1] [--port 8000]
                   [--players N] [--provider gemini|anthropic] [--model M]
                   [--summaries / --no-summaries]
```

Starts uvicorn. Opening `http://host:port/` serves `index.html`. A new game's
options (player count, seed, provider) are chosen on a start screen and sent
when the WebSocket opens, so one running server can play many games. A random
seed is generated per game and shown (reproducibility preserved).

### `GameSession` (`session.py`) — the async driver

One `GameSession` per WebSocket connection. It owns:

- the `GameState` (server-side ground truth, never serialized whole),
- the `agents: dict[int, Agent]` — `LLMAgent` for NPC seats, and a
  **`WebHumanAgent`** for the human seat,
- an `asyncio.Queue` of inbound client messages (night choice, statement, vote).

Because `LLMAgent` methods are **synchronous and blocking** (network LLM calls),
the session runs each agent call via `await asyncio.to_thread(...)` so the event
loop stays free to stream output and receive input. The driver mirrors the
phases of `cli.run_game` but emits protocol events instead of printing:

1. **Setup** → emit `game_start` (seed, player count, roster with names/avatars,
   the human's seat) and `your_role` (human's dealt role + intro text).
2. **Night** → for each role in `WAKE_ORDER` present in the deck:
   emit `night_wake` (role label). If it is the human's seat and there is a real
   choice, emit `night_prompt` (legal actions, human-readable) and `await` the
   human's `night_choice`; otherwise the LLM/`NoAction` agent acts via
   `to_thread`. Emit `night_sleep`. After the loop, emit `night_result` with the
   human's `PrivateView` observations only.
3. **Discussion** (3 rounds) → emit `round_start`; for each seat in order emit
   `speaker_thinking`, get the statement (human via prompt+await, LLM via
   `to_thread`), append `"Player {seat}: {statement}"` to `state.public_log`
   exactly as today, then emit `statement` (full text — the client animates the
   typing). Optionally emit `round_summary` (see below).
4. **Voting** → emit `vote_prompt` to the human and collect their vote; collect
   LLM votes via `to_thread`; then emit `votes_revealed` (all targets at once).
5. **Reveal** → run `resolve_vote` + `evaluate_win`; emit `reveal` with deaths,
   every seat's dealt **and** final role, the outcome, winners, and whether the
   human won.

If the socket drops mid-game, the session is discarded (no persistence in this
phase).

### `WebHumanAgent` (in `session.py` or `agents.py`)

Implements the existing `Agent` protocol but is **async-backed**: its
`night_action` / `speak` / `vote` await the next matching client message from
the session's queue instead of reading stdin. Keep the same validation the
`HumanAgent` and engine already enforce (legal action membership, legal vote
target) and re-prompt on invalid input by emitting an `invalid_input` event.

> Note: the engine's `run_night`/`run_day` call agents synchronously, so the web
> session does **not** call those; it runs the equivalent loop with `await`. This
> is orchestration only — no engine ground-truth logic is duplicated.

### `personas.py`

A fixed pool of cosmetic identities, e.g.
`("Mara", "🦉"), ("Bso", "🦊"), ("Iris", "🐈"), …` (≥ 7 entries). At setup,
assign one to each **non-human** seat deterministically from the seed. Display is
`"Mara"` with the seat number as a small secondary tag (`P3`) so the human can
reconcile names with the seat-numbered statements LLM agents produce. The human
seat is labeled "You". Names never depend on or hint at role.

### `protocol.py`

Typed message schemas, serialized to JSON. Server→client events (names above):
`game_start`, `your_role`, `night_wake`, `night_prompt`, `night_sleep`,
`night_result`, `round_start`, `speaker_thinking`, `statement`, `round_summary`,
`vote_prompt`, `votes_revealed`, `reveal`, `invalid_input`. Client→server
messages: `start_game` (options), `night_choice` (action index), `statement`
(text), `vote` (seat). Reuse the human-readable action/observation strings from
`agents._describe_action` / `_describe_observation` (consider lifting those into
a shared module so both CLI and web format identically).

---

## Frontend design

Single page, no router, no build step. `app.js` opens the WebSocket, keeps a
small client-side game model, and renders four persistent regions plus a phase
overlay. Target a clean desktop layout (responsive niceties optional).

### Persistent layout

```
+----------------------------------------------------------+
|  PHASE BANNER:  Discussion · Round 2 of 3                 |  <- always visible
+----------------+-----------------------------------------+
|  YOU            |  TABLE / TRANSCRIPT                     |
|  [role art]     |  +-- Mara (P1) -----------------+       |
|  SEER           |  | R1: "I'm the Robber..."       |       |
|  - saw P3 =     |  | R2: "P2 is being quiet"       |       |
|    Werewolf     |  +-------------------------------+       |
|                 |  +-- You (P0) -------------------+       |
|  PLAYERS        |  | R1: "I peeked P3, wolf!"      |       |
|  • You    P0    |  +-------------------------------+       |
|  • Mara   P1 ✓  |  +-- Iris (P3) — typing… --------+       |
|  • Bso    P2    |  | R2: "I think P0 is lying▍"     |       |
|  • Iris   P3 …  |  +-------------------------------+       |
+----------------+-----------------------------------------+
|  INPUT BAR: [ your statement…………… ] [ Send ]            |
+----------------------------------------------------------+
```

- **Phase banner** (top): current phase + round/progress. Updates on
  `round_start`, `vote_prompt`, `reveal`, etc.
- **You panel** (left, pinned): the human's role art (illustrated card) + role
  name + a running list of their observations, rendered from `night_result`.
  This is the thing the CLI loses to scrollback — it must never disappear.
- **Players panel** (left): roster of names + avatars + seat tags, with status
  chips — `…` (thinking), `✓` (spoke this round), `voted`, `you`.
- **Transcript (main): grouped per player.** Each player has a card/column that
  accumulates *their* statements labeled by round (`R1`, `R2`, `R3`), instead of
  one interleaved thread. The currently-speaking player's card shows a "typing…"
  state and the streamed text.
- **Input bar** (bottom): context-sensitive. Hidden when it is not the human's
  turn; shows numbered clickable buttons during a `night_prompt`, a text box
  during the human's speaking turn, and clickable player targets during
  `vote_prompt`.

### Streamed typing effect

The server sends each agent's **full** statement in one `statement` event (this
keeps the `Agent` interface untouched — no streaming LLM caller needed). The
client renders it with a typewriter animation (append characters on a timer),
showing a "typing…" indicator on that player's card until done. This looks
identical to live streaming and is far simpler. (Real token streaming from the
LLM is a possible Phase 4 upgrade.)

### Night phase on screen

A dimmed/"night" overlay. As each role wakes (`night_wake`), narrate it
("The Seer wakes…"). On the human's turn, the input area presents the legal
actions as buttons (using `_describe_action` text). Non-human actions just show
the narration — never their choices. After morning, the You panel fills with the
human's observations and the overlay lifts.

### Reveal screen

A celebratory/atmospheric end screen driven by the `reveal` event: each seat's
card flips to show its **final** role art (with a "dealt X → now Y" note when it
changed), deaths highlighted, the outcome banner (Village / Werewolves / No
winner) with the one-line reason, winners listed, and a clear "You won / You
lost". A "Play again" button reopens the start screen.

---

## Visual design — illustrated role art

- Convention: `web/static/assets/roles/{role_value}.png` (e.g. `werewolf.png`,
  `seer.png`, `robber.png`, `troublemaker.png`, `drunk.png`, `insomniac.png`,
  `minion.png`, `villager.png`). Also a face-down `card_back.png` for hidden
  cards during night.
- **Fallback:** if an asset is missing, render a CSS card with the role emoji +
  name so the app is fully playable before art exists. Suggested emoji:
  🐺 werewolf · 😈 minion · 🔮 seer · 🦝 robber · 🤹 troublemaker · 🍺 drunk ·
  😴 insomniac · 🧑‍🌾 villager.
- Theme: dark, moonlit palette (indigo/violet), a soft glow on the active
  speaker's card, subtle card-flip on reveal. Keep it CSS — no animation libs.
- **Assets are sourced/generated separately.** This phase ships the fallback and
  the loading convention; dropping PNGs into the folder later "just works." Note
  licensing: do not ship copyrighted official ONUW art; use original/generated
  illustrations.

---

## Round summaries (nice-to-have, behind a flag)

After each discussion round, optionally show a one-line neutral recap (e.g.
"Round 2: Mara doubled down as Robber; Bso accused P0; Iris stayed vague.").

- Gated by `--summaries / --no-summaries` (default **on**, trivially cuttable).
- Generated by **one extra LLM call** over the round's **public** statements only
  (no roles, no reasoning — same information any spectator has), via a small
  neutral "narrator" prompt. Reuse the existing `LLMCaller`.
- Emitted as a `round_summary` event and shown as a divider in the transcript /
  under the round in each player card.
- If it proves awkward or slow, ship with `--no-summaries` and move it to a
  follow-up — the rest of the phase does not depend on it.

---

## Tests

Engine tests must still pass unchanged. Add `tests/test_web.py` covering the
**orchestration and boundary**, not the browser:

- **Boundary:** serialized server→client payloads for `game_start`,
  `night_result`, `statement`, `round_start`, `votes_revealed` contain **no**
  other player's role, no center cards, and no agent `reasoning`. A reveal
  payload *does* include final roles (only after `evaluate_win`).
- **`WebHumanAgent`:** feeding queued `night_choice` / `statement` / `vote`
  messages drives the same `Action` / statement / vote a `HumanAgent` would, and
  invalid input triggers `invalid_input` + re-prompt without advancing state.
- **Session flow:** with all-`ScriptedAgent`/`RandomAgent` seats and a fake
  human queue, a full game runs to a `reveal` event whose outcome matches
  `evaluate_win` on the same seed (reproducibility holds).
- **Personas:** name/avatar assignment is deterministic from the seed and never
  collides with role identity (names independent of `dealt_roles`).
- **Summaries:** with `--no-summaries`, no `round_summary` event is emitted; with
  it on, exactly one per round, built only from public statements.

Use a fake `LLMCaller` (returns canned strings) so tests need no network/API key.

---

## Out of scope for Phase 3 (Phase 4+)

- **Voice I/O** — TTS for agents, STT for the human (the original Phase 3 idea,
  now the natural Phase 4 given the browser already has mic/speaker access).
- Real token-level LLM streaming (current phase fakes typing client-side).
- Multiplayer / multiple human browsers, accounts, lobbies.
- Persistence / reconnect mid-game, spectator mode.
- Multi-game session scoring across N games.
- New roles (Mason, Hunter) — unchanged from the Phase 2 backlog.
```
