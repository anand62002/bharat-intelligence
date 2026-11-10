const https = require("https");

// ARIA's model is chosen HERE, server-side — never by the browser. This mirrors
// HAIKU_MODEL in data/model_config.py (the Python single source of truth);
// Vercel functions can't import Python, so keep the two in sync.
// Haiku 5.5 thinks by default, which would put a `thinking` block first and
// spend ARIA's 1000-token budget — chat replies don't need it, so it's off.
const ARIA_MODEL = "claude-haiku-5-5";
const ARIA_THINKING = { type: "disabled" };

module.exports = async function handler(req, res) {
  if (req.method !== "POST") {
    return res.status(405).json({ error: "Method not allowed" });
  }

  const apiKey = process.env.ANTHROPIC_API_KEY;
  if (!apiKey) {
    return res.status(500).json({ error: "ANTHROPIC_API_KEY not configured" });
  }

  let payload;
  try {
    payload = typeof req.body === "string" ? JSON.parse(req.body) : { ...(req.body || {}) };
  } catch (e) {
    return res.status(400).json({ error: "Invalid JSON body" });
  }
  // Override whatever the client sent — the model is not a client decision.
  payload.model = ARIA_MODEL;
  payload.thinking = ARIA_THINKING;
  const body = JSON.stringify(payload);

  return new Promise((resolve) => {
    const options = {
      hostname: "api.anthropic.com",
      path: "/v1/messages",
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "Content-Length": Buffer.byteLength(body),
        "x-api-key": apiKey,
        "anthropic-version": "2023-06-01",
      },
    };

    const request = https.request(options, (response) => {
      let data = "";
      response.on("data", (chunk) => { data += chunk; });
      response.on("end", () => {
        try {
          res.status(response.statusCode).json(JSON.parse(data));
        } catch (e) {
          res.status(500).json({ error: "Invalid response from Anthropic" });
        }
        resolve();
      });
    });

    request.on("error", (e) => {
      res.status(500).json({ error: e.message });
      resolve();
    });

    request.write(body);
    request.end();
  });
};
