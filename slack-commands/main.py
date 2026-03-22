import logging
from typing import Callable, Coroutine
from urllib.parse import parse_qs

from fastapi import BackgroundTasks, Depends, FastAPI, Response

from routers import db_control, gke_control, open_issues, publish_packages
from adapters.slack_verify import verify_slack_request

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s %(message)s")

app = FastAPI(docs_url=None, redoc_url=None)

CommandHandler = Callable[..., Coroutine]

COMMANDS: dict[str, CommandHandler] = {
    "/open-issues": open_issues.handle,
    "/db-start": db_control.handle_start,
    "/db-stop": db_control.handle_stop,
    "/gke-up": gke_control.handle_up,
    "/gke-down": gke_control.handle_down,
    "/publish-packages": publish_packages.handle,
}


@app.get("/health")
async def health() -> dict:
    return {"status": "ok"}


@app.post("/slack/commands")
async def slack_commands(
    background_tasks: BackgroundTasks,
    body: bytes = Depends(verify_slack_request),
) -> Response:
    form = parse_qs(body.decode())
    command = form.get("command", [""])[0] if form.get("command") else ""
    response_url = form.get("response_url", [""])[0] if form.get("response_url") else ""
    text = form.get("text", [""])[0] if form.get("text") else ""

    handler = COMMANDS.get(command)
    if handler is None:
        return Response(
            content=f"Unknown command: {command}",
            media_type="text/plain",
            status_code=200,
        )

    if response_url:
        background_tasks.add_task(handler, response_url, text)

    return Response(
        content='{"response_type":"ephemeral","text":"処理中..."}',
        media_type="application/json",
        status_code=200,
    )
