"""Web server for the Tide Pool Activity: static page + token swap + server-checked games."""
import os
import secrets
import time
from pathlib import Path
from typing import Awaitable, Callable

import aiohttp
from aiohttp import web

import tidepool

DIST = Path(__file__).parent / "web" / "dist"
API = "https://discord.com/api/v10"
GAME_TTL = 15 * 60
PREFIX = "/tidepool"  # path on shiraishi's public address

# (user_id, guild_id, channel_id, result) -> extra fields for the page (gain etc.)
ResultHook = Callable[[int, int | None, int | None, dict], Awaitable[dict]]


def make_app(on_result: ResultHook, client_id: str, client_secret: str, dev: bool = False) -> web.Application:
    sessions: dict[str, tuple[int, float]] = {}  # access token -> (user id, checked at)
    games: dict[str, dict] = {}

    async def user_for(request: web.Request) -> int:
        token = request.headers.get("Authorization", "").removeprefix("Bearer ").strip()
        if dev and token == "dev":
            return 0
        hit = sessions.get(token)
        if hit and time.time() - hit[1] < 600:
            return hit[0]
        # ask Discord who this token belongs to
        async with aiohttp.ClientSession() as s:
            async with s.get(f"{API}/users/@me", headers={"Authorization": f"Bearer {token}"}) as r:
                if r.status != 200:
                    raise web.HTTPUnauthorized()
                uid = int((await r.json())["id"])
        sessions[token] = (uid, time.time())
        return uid

    def snowflake(v) -> int | None:
        return int(v) if isinstance(v, str) and v.isdigit() else None

    async def config(_):
        return web.json_response({"client_id": client_id, "dev": dev})

    async def token(request: web.Request):
        body = await request.json()
        form = {"client_id": client_id, "client_secret": client_secret,
                "grant_type": "authorization_code", "code": str(body.get("code", ""))}
        async with aiohttp.ClientSession() as s:
            async with s.post(f"{API}/oauth2/token", data=form) as r:
                data = await r.json()
                if r.status != 200 or "access_token" not in data:
                    raise web.HTTPBadRequest(text="token exchange failed")
        return web.json_response({"access_token": data["access_token"]})

    async def new_game(request: web.Request):
        uid = await user_for(request)
        body = await request.json()
        now = time.time()
        for gid in [g for g, v in games.items() if now - v["at"] > GAME_TTL]:
            del games[gid]
        p = tidepool.make_puzzle()
        gid = secrets.token_urlsafe(12)
        games[gid] = {"p": p, "uid": uid, "at": now,
                      "guild": snowflake(body.get("guild_id")), "channel": snowflake(body.get("channel_id"))}
        return web.json_response({"id": gid, "w": p["w"], "h": p["h"], "r0": p["r0"], "r1": p["r1"],
                                  "grid": p["grid"], "best": p["best"]})

    async def result(request: web.Request):
        uid = await user_for(request)
        body = await request.json()
        game = games.get(str(body.get("id")))
        if not game or game["uid"] != uid:
            raise web.HTTPNotFound(text="no such game")
        grid = body.get("grid")
        if not tidepool.valid_grid(game["p"], grid):
            raise web.HTTPBadRequest(text="bad board")
        del games[str(body["id"])]  # one result per game
        res = tidepool.flow_check(game["p"], grid)
        extra = await on_result(uid, game["guild"], game["channel"], {**res, "best": game["p"]["best"]})
        return web.json_response({"won": res["won"], "order": res["order"], "tiles": res["tiles"], **extra})

    async def index(_):
        return web.FileResponse(DIST / "index.html")

    app = web.Application(client_max_size=64 * 1024)
    # tailscale serve may or may not strip /tidepool, so answer on both
    for pre in ("", PREFIX):
        app.router.add_get(f"{pre}/api/config", config)
        app.router.add_post(f"{pre}/api/token", token)
        app.router.add_post(f"{pre}/api/new", new_game)
        app.router.add_post(f"{pre}/api/result", result)
        app.router.add_get(f"{pre}/", index)
        app.router.add_static(f"{pre}/assets", DIST / "assets")
    return app


async def start(on_result: ResultHook, port: int) -> web.AppRunner | None:
    client_id, secret = os.environ.get("DISCORD_CLIENT_ID", ""), os.environ.get("DISCORD_CLIENT_SECRET", "")
    dev = os.environ.get("ACTIVITY_DEV") == "1"
    if not (client_id and secret) and not dev:
        print("activity: DISCORD_CLIENT_ID / DISCORD_CLIENT_SECRET not set, Tide Pool web server off", flush=True)
        return None
    runner = web.AppRunner(make_app(on_result, client_id, secret, dev))
    await runner.setup()
    # local only; tailscale funnel makes it public
    await web.TCPSite(runner, "127.0.0.1", port).start()
    print(f"activity: serving Tide Pool on 127.0.0.1:{port}{' (dev mode)' if dev else ''}", flush=True)
    return runner
