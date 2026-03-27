const MAX_AGE_SECONDS = 5 * 60;

function hexEncode(buffer: ArrayBuffer): string {
  return [...new Uint8Array(buffer)]
    .map((b) => b.toString(16).padStart(2, "0"))
    .join("");
}

export async function verifySlackRequest(
  body: string,
  headers: Headers,
  signingSecret: string,
): Promise<boolean> {
  const timestamp = headers.get("X-Slack-Request-Timestamp");
  const signature = headers.get("X-Slack-Signature");

  if (!timestamp || !signature) return false;

  const ts = parseInt(timestamp, 10);
  if (isNaN(ts) || Math.abs(Date.now() / 1000 - ts) > MAX_AGE_SECONDS) {
    return false;
  }

  const key = await crypto.subtle.importKey(
    "raw",
    new TextEncoder().encode(signingSecret),
    { name: "HMAC", hash: "SHA-256" },
    false,
    ["sign"],
  );

  const baseString = `v0:${timestamp}:${body}`;
  const mac = await crypto.subtle.sign(
    "HMAC",
    key,
    new TextEncoder().encode(baseString),
  );

  const expected = `v0=${hexEncode(mac)}`;

  // timing-safe comparison
  if (expected.length !== signature.length) return false;

  const a = new TextEncoder().encode(expected);
  const b = new TextEncoder().encode(signature);
  return crypto.subtle.timingSafeEqual(a, b);
}
