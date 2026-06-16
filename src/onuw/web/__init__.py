"""Phase 3 web UI for One Night Ultimate Werewolf.

A browser front-end over the existing pure engine. The server is the only place
that imports FastAPI; the engine remains stdlib-only. The browser is treated as
"an agent": it receives only the human's private view plus public events, never
the global game state, other players' roles, or any agent's private reasoning.
"""
