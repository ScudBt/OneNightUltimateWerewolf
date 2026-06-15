# CLAUDE.md

## Project: One Night Ultimate Werewolf (ONUW) — multi-agent engine

A text-based implementation of One Night Ultimate Werewolf where LLM agents play
the social-deduction roles. Built in phases. We are currently on **Phase 0**.

## North-star architecture (do not violate)

There is a hard boundary between two layers:

1. **Game engine** — owns ALL ground truth: the deck, the deal, every night-time
   card swap, the final card assignment, vote resolution, and win evaluation.
   The engine is fully deterministic and contains NO LLM calls and NO I/O.
2. **Agent layer** — each agent receives only its own *legal private view* (its
   dealt role + the observations its night action legitimately produced + the
   public game log) and returns actions and votes. An agent NEVER receives the
   true global state.

Rules that follow from this and must always hold:

- The engine never asks an agent what its current role is — the engine knows.
- No agent (and, in later phases, no LLM) is ever passed the full `GameState`.
- All randomness flows through a single seeded RNG provided at construction.
  Given the same seed and the same agent decisions, a game is byte-for-byte
  reproducible. This is required for tests now and for evaluation later.

## Tech stack

- Python 3.11+
- Standard library + `pytest` for tests. No web framework, no LLM SDK, and no
  async in Phase 0. Keep dependencies minimal.
- Use `dataclasses` and `enum.Enum`. Type-annotate everything; code should pass
  `mypy --strict` if run.
- Environment managed with `uv` (preferred) or a plain `venv`.
- **API keys** (`ANTHROPIC_API_KEY`, `GEMINI_API_KEY`) live in the repo-root
  `.env` (gitignored) and are auto-loaded via `onuw._env.load_env()`. Never ask
  the user to paste a key in the terminal; always rely on `.env`.

## Conventions

- Module layout under `src/onuw/`: `roles.py`, `observations.py`, `state.py`,
  `engine.py`, `agents.py`, `game.py` (orchestrator).
- Pure functions where possible. The engine must not `print` or read input.
  Any user-facing output belongs in a separate `cli.py` later — not the engine.
- Tests live in `tests/`, mirror the module names, and must cover every night
  action's effect on both card state and observations, the ordering
  interactions between actions, vote-resolution edge cases, and each
  win-condition branch.
- Run tests with `pytest -q`. All tests must pass before a task is "done".

## How to work on this repo

- For any non-trivial task, propose a plan first and wait for approval before
  editing files.
- Make the smallest change that satisfies the current task. Do not implement
  future phases ahead of time.
- After implementing, run `pytest -q` and report the result. If you add
  behavior, add tests for it in the same change.
- When a rule is ambiguous, ask rather than guessing — game-logic guesses are
  expensive to debug once LLM agents are layered on top.

## Phase roadmap (context only — do only the current phase)

- **Phase 0 (done):** deterministic engine + scripted/random agents. No LLM.
- **Phase 1 (done):** LLM agents (Anthropic + Gemini), one-round discussion, speak/vote.
- **Phase 2 (current):** playable CLI game — HumanAgent (stdin), Minion + Drunk roles,
  3-round discussion, 4–7 player presets, end-of-game reveal. Entry point: `cli.py`.
- Phase 3: voice I/O (speech-to-text for the human, text-to-speech for agents).
- Phase 4: status UI presenting per-player/role state.
