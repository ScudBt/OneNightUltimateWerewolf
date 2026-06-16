# One Night Ultimate Werewolf — multi-agent engine

A text/web implementation of *One Night Ultimate Werewolf* where LLM agents play
the social-deduction roles. A deterministic, pure-Python game engine owns all
ground truth (the deal, every night swap, vote resolution, win evaluation); the
agent layer only ever sees its own legal private view. You play a seat at the
table against LLM opponents — perform your night action, talk through the day,
and vote to find the werewolves.

Built in phases: deterministic engine → LLM agents → playable CLI → **browser UI**.

## Setup

Requires Python 3.11+ and [`uv`](https://docs.astral.sh/uv/).

```bash
uv sync
```

API keys live in a gitignored `.env` at the repo root and are loaded
automatically — never pass keys on the command line:

```
ANTHROPIC_API_KEY=...
GEMINI_API_KEY=...
```

## Run the game

### Web UI (recommended)

```bash
uv run python -m onuw.web
```

Then open <http://127.0.0.1:8000/> and click **Begin the night**. Pick player
count, opponent provider, optional model, and an optional seed on the start
screen.

Options:

| Flag | Default | Notes |
|---|---|---|
| `--provider` | `gemini` | `gemini` or `anthropic` |
| `--model` | provider default | e.g. `gemini-3.5-flash`, `claude-sonnet-4-6` |
| `--no-summaries` | off | disable per-round AI recaps |
| `--host` / `--port` | `127.0.0.1` / `8000` | |

```bash
# stronger opponents:
uv run python -m onuw.web --provider gemini --model gemini-3.5-flash
```

Each game writes a full transcript (statements, private reasoning, votes,
reveal) to `runs/web-<timestamp>-seed<N>.txt`.

> Tip: `gemini-3.5-flash` plays markedly better than the cheap default
> `gemini-2.5-flash-lite`. Switch with `--model` or the start-screen box.

### Terminal CLI

```bash
uv run python -m onuw.cli --players 5
# --seed N (reproducible)  --provider anthropic  --model <id>
```

## Tests

```bash
uv run pytest -q
```
