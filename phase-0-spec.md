# Phase 0 Spec — Deterministic ONUW Engine

> **How to use this:** start Claude Code in the repo root (with `CLAUDE.md`
> present), enter plan mode (`shift+tab`), and paste this spec as your prompt:
> *"Implement Phase 0 per `docs/phase-0-spec.md`. Propose a plan first."*
> Review the plan before approving any edits.

## Goal

A fully deterministic game engine that can play a complete game of One Night
Ultimate Werewolf end to end, driven by scripted or random agents. **No LLM, no
network, no async, no real I/O.** This phase exists to lock down correct game
logic before any model is attached.

## Scope: role set

Support an arbitrary valid role multiset where `len(roles) == player_count + 3`
(three cards go to the center). The **default scenario** is a 5-player game with
this multiset (8 cards total):

- Werewolf ×2
- Seer ×1
- Robber ×1
- Troublemaker ×1
- Insomniac ×1
- Villager ×2

This set is deliberately minimal but exercises every hard mechanic: shared
hidden knowledge (werewolves), the lone-wolf center peek, information-gathering
(Seer), a swap-and-see (Robber), a blind swap of two others (Troublemaker), and
an end-of-night self-check (Insomniac). Do **not** implement Doppelgänger,
Minion, Mason, Drunk, Hunter, or Tanner yet — but keep the design open to them.

## Data model

- **Positions** are integers. `0 .. player_count-1` are player seats;
  `player_count .. player_count+2` are the three center slots. Treat all
  positions uniformly so swaps are just index operations.
- `Role(Enum)`: the roles above.
- `GameConfig`: `player_count: int`, `roles: list[Role]` (length must equal
  `player_count + 3`), `seed: int`.
- `GameState`:
  - `dealt_roles: dict[int, Role]` — the original deal, immutable after setup.
  - `current_roles: dict[int, Role]` — mutable; evolves as night actions resolve.
  - `public_log: list[str]` — events any player could observe (kept minimal in
    Phase 0).
  - It must be possible to derive each player's private view from the engine;
    `GameState` itself is engine-internal and never handed to an agent.

## Observation schema

Each night action appends a typed `Observation` to the acting player's private
record. The agent layer (Phase 1) will consume exactly these. Suggested variants
(use a tagged dataclass / union):

- `SawWerewolves(wolf_positions: list[int])` — seats of all werewolves (excluding
  self). Empty list means "I am the lone wolf."
- `LoneWolfPeek(center_position: int, role: Role)` — only if exactly one
  werewolf is in play among players.
- `SeerPeekedPlayer(target: int, role: Role)`
- `SeerPeekedCenter(targets: list[int], roles: list[Role])` — exactly two center
  positions.
- `Robbed(target: int, new_role: Role)` — the role the robber now holds after
  swapping.
- `TroublemakerSwapped(target_a: int, target_b: int)` — no roles revealed.
- `InsomniacWoke(final_role: Role)` — the robber's/troublemaker's effects are
  already applied, so this reflects the true end-of-night card.

A **private view** = `dealt_role` + ordered `list[Observation]` for that player
+ a copy of `public_log`.

## Night phase — wake order and exact semantics

Roles act in this fixed order; skip any role not present in the current game.
Card swaps mutate `current_roles`; observations are recorded at the moment the
action happens, so they reflect the state *at that point in the sequence*.

1. **Werewolves** — all players whose **dealt** role is Werewolf "wake together":
   each records `SawWerewolves` listing the other werewolf seats. If there is
   exactly one werewolf, it instead/also records a `LoneWolfPeek` of one center
   card (choose via the agent; default random center slot).
2. **Seer** — chooses either one player position (not self) → `SeerPeekedPlayer`,
   or two of the three center positions → `SeerPeekedCenter`.
3. **Robber** — chooses one player position (not self), swaps `current_roles`
   between self and target, then records `Robbed(target, new_role)` where
   `new_role` is what it now holds. The robbed player now holds the Robber card
   but is **not** notified.
4. **Troublemaker** — chooses two player positions, both other than self, and
   swaps their `current_roles`. Records `TroublemakerSwapped` with no role info.
5. **Insomniac** — reads `current_roles[self]` (post-all-swaps) → `InsomniacWoke`.

### The rule that must not be gotten wrong

- A player's **night action is determined by their DEALT role** (`dealt_roles`).
- A player's **team for winning is determined by the card they hold at the END**
  (`current_roles` after all night actions).

So if the player dealt Robber robs a Werewolf, they perform the Robber action at
night, but they finish on the **werewolf** team. The player they robbed finishes
holding Robber (a village-team card) and never acted. Encode this explicitly and
test it.

## Day phase

In Phase 0 this is a **stub**: no real discussion. Optionally let each agent emit
one templated public statement appended to `public_log` (e.g., a claimed role),
but agents may also pass. Keep it trivial — discussion is Phase 1's job.

## Voting

- Every player casts exactly one vote for a player other than themselves.
- Tally votes. Let `max_votes` be the highest count any player received.
- If `max_votes <= 1`: **no one dies** (total confusion).
- Otherwise: **every** player whose count equals `max_votes` dies (ties → all of
  them die).
- Implement this as one isolated, well-tested function; rule variants exist and
  you may want to swap it later.

## Win evaluation (evaluated on END-of-night `current_roles`)

Let `werewolves_present` = any **player seat** (not center) holds Werewolf at the
end, and `deaths` = set of players killed by the vote.

- If `werewolves_present`:
  - At least one dead player is a Werewolf → **VILLAGE_WIN**.
  - No dead player is a Werewolf → **WEREWOLF_WIN**.
- If not `werewolves_present` (all werewolf cards ended in the center):
  - `deaths` is empty → **VILLAGE_WIN** (correctly sensed no wolf).
  - someone died → **NO_WINNER** (village loses; there is no wolf team to win).

Return a result object with the outcome enum and the set of winning player seats
(useful for evaluation in later phases).

## Agent interface

Define an `Agent` protocol with two methods, each receiving only a private view
plus the set of legal choices the engine computes:

- `night_action(view, legal_actions) -> Action`
- `vote(view, public_log) -> int`  (a target player position)

Provide two reference implementations:

- `RandomAgent(rng)` — picks uniformly among legal night actions and legal vote
  targets. Drives smoke tests and full-game runs.
- `ScriptedAgent(actions, vote_target)` — replays a fixed action list. Essential
  for deterministic scenario tests (e.g., "robber robs seat 2").

The engine computes legal actions; agents only choose among them. An illegal
choice from an agent is a programming error and should raise, not be silently
fixed.

## Orchestrator (`game.py`)

A `play_game(config, agents) -> GameResult` function that runs:
`deal → run_night → run_day(stub) → run_vote → resolve_deaths → evaluate_win`,
threading the seeded RNG throughout and recording observations as it goes.

## Determinism

- One `random.Random(seed)` constructed from `GameConfig.seed`, passed down.
- The deal, any default/random agent choices, and tie-breaking must all draw
  from it. Same seed + same agents ⇒ identical game, asserted by a golden test.

## Testing checklist (all must pass via `pytest -q`)

- **Deal:** `player_count + 3` cards dealt; each role from `roles` used exactly
  once; first `player_count` positions are seats, last 3 are center.
- **Werewolves:** two wolves each see the other's seat; constructed lone-wolf
  case records a `LoneWolfPeek`.
- **Seer:** player-peek returns the target's current role; center-peek returns
  exactly two center roles.
- **Robber:** `current_roles` of robber and target are swapped; `Robbed.new_role`
  equals the target's pre-swap role; robbed player is not notified.
- **Troublemaker:** the two targets' `current_roles` are swapped; no role info
  leaks into the observation.
- **Ordering interactions:**
  - Seer peeks a player, then Robber robs that same player → Seer's recorded
    observation still reflects the **pre-robbery** role.
  - Insomniac who was robbed earlier sees their **post-swap** (new) role.
- **Voting:** all-tied-at-one → no death; clear plurality → that player dies;
  two-way tie at the max → both die.
- **Win conditions:** one test per branch (wolf dies → village; no wolf dies →
  wolves; no wolves present + no death → village; no wolves present + a death →
  no winner). Cover the robber-robs-werewolf case explicitly: the robber is on
  the werewolf team at the end.
- **Determinism:** two `play_game` runs with the same seed and `RandomAgent`s
  produce identical results and identical observation logs.

## Out of scope for Phase 0

LLM calls, networking, async, real discussion, voice, UI, and the roles listed
as excluded above. Do not add them.
