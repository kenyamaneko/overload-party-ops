import { verifySlackRequest } from "./slack-verify";

interface Env {
  SLACK_SIGNING_SECRET: string;
  DISPATCH_SECRET: string;
  CLOUD_RUN_URL: string;
}

function parseResponseUrl(body: string): string | null {
  return new URLSearchParams(body).get("response_url");
}

async function notifySlack(
  responseUrl: string,
  text: string,
): Promise<void> {
  try {
    await fetch(responseUrl, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ response_type: "ephemeral", text }),
    });
  } catch (e) {
    console.error("Failed to notify Slack:", e);
  }
}

async function notifyError(
  body: string,
  message: string,
): Promise<void> {
  const responseUrl = parseResponseUrl(body);
  if (!responseUrl) {
    console.error("response_url not found in body, cannot notify Slack:", message);
    return;
  }
  await notifySlack(responseUrl, `:warning: ${message}`);
}

async function forwardToCloudRun(
  body: string,
  env: Env,
): Promise<void> {
  let resp: Response;
  try {
    resp = await fetch(`${env.CLOUD_RUN_URL}/slack/commands`, {
      method: "POST",
      headers: {
        "Content-Type": "application/x-www-form-urlencoded",
        Authorization: `Bearer ${env.DISPATCH_SECRET}`,
      },
      body,
    });
  } catch (e) {
    console.error("Failed to forward to Cloud Run:", e);
    await notifyError(body, "Cloud Run への転送に失敗しました。ログを確認してください。");
    return;
  }

  if (resp.ok) return;

  const detail = await resp.text();
  console.error(`Cloud Run returned ${resp.status}: ${detail}`);
  await notifyError(body, `Cloud Run への転送に失敗しました (HTTP ${resp.status})`);
}

export default {
  async fetch(
    request: Request,
    env: Env,
    ctx: ExecutionContext,
  ): Promise<Response> {
    if (request.method !== "POST") {
      return new Response("Method Not Allowed", { status: 405 });
    }

    const url = new URL(request.url);
    if (url.pathname !== "/slack/commands") {
      return new Response(`Unknown path: ${url.pathname}`, { status: 404 });
    }

    const body = await request.text();

    const valid = await verifySlackRequest(
      body,
      request.headers,
      env.SLACK_SIGNING_SECRET,
    );
    if (!valid) {
      return new Response("Invalid signature", { status: 403 });
    }

    ctx.waitUntil(forwardToCloudRun(body, env));

    return new Response(
      JSON.stringify({
        response_type: "ephemeral",
        text: "コマンドを受け付けました",
      }),
      {
        status: 200,
        headers: { "Content-Type": "application/json" },
      },
    );
  },
} satisfies ExportedHandler<Env>;
