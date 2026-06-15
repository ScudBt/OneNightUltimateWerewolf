"""Entry point: ``python -m onuw.web [--host H] [--port P] ...``"""
from __future__ import annotations

import argparse

import uvicorn

from onuw.web.server import _DEFAULT_MODELS, app


def main() -> None:
    parser = argparse.ArgumentParser(description="One Night Ultimate Werewolf (web UI)")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument(
        "--provider", default="gemini", choices=["gemini", "anthropic"],
        help="Default LLM provider for NPC agents (default: gemini)",
    )
    parser.add_argument("--model", default=None, help="Default model ID override")
    parser.add_argument(
        "--no-summaries", action="store_true",
        help="Disable per-round LLM summaries",
    )
    args = parser.parse_args()

    app.state.provider = args.provider
    app.state.model = args.model or _DEFAULT_MODELS[args.provider]
    app.state.summaries = not args.no_summaries

    print(f"Open http://{args.host}:{args.port}/ to play.")
    uvicorn.run(app, host=args.host, port=args.port, log_level="warning")


if __name__ == "__main__":
    main()
