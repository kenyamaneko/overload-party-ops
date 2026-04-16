import logging
from typing import Callable, Coroutine
from urllib.parse import parse_qs

from fastapi import BackgroundTasks, Depends, FastAPI, Response

from routers import db_control, gke_control, open_issues
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

    user_id = form.get("user_id", [""])[0] if form.get("user_id") else ""

    handler = COMMANDS.get(command)
    if handler is None:
        if response_url:
            background_tasks.add_task(
                post_in_channel, response_url, f"未対応のコマンドです: `{command}`",
            )
        return Response(content='{"status":"accepted"}', media_type="application/json", status_code=200)

    if response_url:
        background_tasks.add_task(_run_handler, handler, response_url, text, command, user_id)

    return Response(content='{"status":"accepted"}', media_type="application/json", status_code=200)


async def _run_handler(
    handler: CommandHandler, response_url: str, text: str, command: str, user_id: str,
) -> None:
    by = f" (by <@{user_id}>)" if user_id else ""
    await post_in_channel(response_url, f"`{command}` の処理を開始します...{by}")
    await handler(response_url, text)
