"""FastAPI app: serves the static front-end and drives one game per WebSocket."""
from __future__ import annotations

import asyncio
import random
from datetime import datetime
from pathlib import Path
from typing import Any, Awaitable, Callable, Optional, TextIO

from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from starlette.responses import Response

from onuw._env import load_env
from onuw.presets import PRESETS
from onuw.web import protocol
from onuw.web.session import ClientGone, GameOptions, GameSession

load_env()  # bridge .env keys into os.environ before any LLM client is built

_STATIC = Path(__file__).parent / "static"

_DEFAULT_MODELS = {
    "gemini": "gemini-2.5-flash-lite",
    "anthropic": "claude-sonnet-4-6",
}

app = FastAPI(title="One Night Ultimate Werewolf")

# Server-side defaults; overridden by __main__ when launched from the CLI.
app.state.provider = "gemini"
app.state.model = _DEFAULT_MODELS["gemini"]
app.state.summaries = True

app.mount("/static", StaticFiles(directory=_STATIC), name="static")


@app.middleware("http")
async def _no_cache(
    request: Request, call_next: Callable[[Request], Awaitable[Response]]
) -> Response:
    # Local single-player dev server: never let the browser serve stale JS/CSS
    # (a cached app.js was rendering deck entries as "[object Object]").
    response = await call_next(request)
    response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
    return response


@app.get("/")
async def index() -> FileResponse:
    return FileResponse(_STATIC / "index.html")


def _open_log(seed: int) -> Optional[TextIO]:
    try:
        runs = Path("runs")
        runs.mkdir(exist_ok=True)
        stamp = datetime.now().strftime("%Y-%m-%dT%H-%M-%S")
        return open(runs / f"web-{stamp}-seed{seed}.txt", "w", encoding="utf-8")
    except OSError:
        return None


async def _pump(ws: WebSocket, queue: "asyncio.Queue[dict[str, Any]]") -> None:
    """Forward inbound client messages into the session's queue."""
    try:
        while True:
            queue.put_nowait(await ws.receive_json())
    except WebSocketDisconnect:
        queue.put_nowait({"type": "__disconnect__"})
    except Exception:
        queue.put_nowait({"type": "__disconnect__"})


def _options_from_start(msg: dict[str, Any]) -> GameOptions:
    players = msg.get("players", 5)
    if players not in PRESETS:
        players = 5
    provider = str(msg.get("provider") or app.state.provider)
    # Precedence: model the client picked > the server's --model default (when the
    # provider matches what the server launched with) > that provider's built-in default.
    client_model = msg.get("model")
    if client_model:
        model = str(client_model)
    elif provider == app.state.provider:
        model = str(app.state.model)
    else:
        model = _DEFAULT_MODELS.get(provider, app.state.model)
    raw_seed = msg.get("seed")
    seed = int(raw_seed) if isinstance(raw_seed, int) else random.randint(0, 2**31)
    summaries = bool(msg.get("summaries", app.state.summaries))
    return GameOptions(
        player_count=int(players),
        seed=seed,
        provider=provider,
        model=model,
        summaries=summaries,
    )


@app.websocket("/ws")
async def ws(websocket: WebSocket) -> None:
    await websocket.accept()
    try:
        first = await websocket.receive_json()
    except WebSocketDisconnect:
        return
    if not isinstance(first, dict) or first.get("type") != "start_game":
        await websocket.close()
        return

    options = _options_from_start(first)
    inbound: "asyncio.Queue[dict[str, Any]]" = asyncio.Queue()

    async def send(message: dict[str, Any]) -> None:
        await websocket.send_json(message)

    log_file = _open_log(options.seed)
    session = GameSession(send, inbound, options, log_file=log_file)
    receiver = asyncio.create_task(_pump(websocket, inbound))
    try:
        await session.run()
    except (ClientGone, WebSocketDisconnect):
        pass
    except Exception as exc:  # surface engine/LLM errors to the client
        try:
            await send(protocol.error(str(exc)))
        except Exception:
            pass
    finally:
        receiver.cancel()
        if log_file is not None:
            log_file.close()
