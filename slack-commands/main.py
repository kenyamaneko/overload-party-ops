import logging
from typing import Callable, Coroutine
from urllib.parse import parse_qs

from fastapi import BackgroundTasks, Depends, FastAPI, Response

from routers import db_control, gke_control, open_issues, publish_gamedata_pkg
from adapters.worker_auth import verify_dispatch_request
from adapters.slack_response import post_in_channel

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s %(message)s")

app = FastAPI(docs_url=None, redoc_url=None)

CommandHandler = Callable[..., Coroutine]

COMMANDS: dict[str, CommandHandler] = {
    "/open-issues": open_issues.handle,
    "/db-start": db_control.handle_start,
    "/db-stop": db_control.handle_stop,
    "/gke-up": gke_control.handle_up,
    "/gke-down": gke_control.handle_down,
    "/publish-gamedata-pkg": publish_gamedata_pkg.handle,
}


@app.get("/health")
async def health() -> dict:
    return {"status": "ok"}


@app.post("/slack/commands")
async def slack_commands(
    background_tasks: BackgroundTasks,
    body: bytes = Depends(verify_dispatch_request),
) -> Response:
    form = parse_qs(body.decode())
    command = form.get("command", [""])[0] if form.get("command") else ""
    response_url = form.get("response_url", [""])[0] if form.get("response_url") else ""
    text = form.get("text", [""])[0] if form.get("text") else ""

    handler = COMMANDS.get(command)
    if handler is None:
        if response_url:
            background_tasks.add_task(
                post_in_channel, response_url, f"未対応のコマンドです: `{command}`",
            )
        return Response(content='{"status":"accepted"}', media_type="application/json", status_code=200)

    if response_url:
        background_tasks.add_task(_run_handler, handler, response_url, text)

    return Response(content='{"status":"accepted"}', media_type="application/json", status_code=200)


async def _run_handler(handler: CommandHandler, response_url: str, text: str) -> None:
    """「処理を開始します...」を通知してからハンドラを実行する。"""
    await post_in_channel(response_url, "処理を開始します...")
    await handler(response_url, text)
