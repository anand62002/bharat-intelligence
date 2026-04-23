/**
 * dashboard/api/research.js  --  Research Proposals API Route
 * =============================================================
 * Next.js serverless API handler that exposes research_proposals
 * from Supabase to the ARIA dashboard.
 *
 * Endpoints
 * ---------
 *   GET  /api/research?status=pending&limit=20&min_relevance=0
 *        List research proposals (ordered by relevance desc)
 *
 *   GET  /api/research?id=<uuid>
 *        Get a single proposal (full detail including debate_log)
 *
 *   PATCH /api/research  { id, status: "approved"|"rejected" }
 *        Update proposal status (triggers Python approve_proposal via note)
 *
 * Environment variables required (same as other API routes)
 * ---------------------------------------------------------
 *   SUPABASE_URL          Supabase project URL
 *   SUPABASE_SERVICE_KEY  Service role key (bypasses RLS)
 *
 * Usage from ARIA chat
 * --------------------
 *   ARIA automatically calls this endpoint when the user says:
 *     "Show me pending research proposals"
 *     "Approve proposal [ID]"
 *     "Reject proposal [ID]"
 *
 *   After approving, run on the Python backend:
 *     python governance/research_agent.py --approve <id>
 */

const https = require("https");

// ---------------------------------------------------------------------------
// Supabase REST helper (uses native https to avoid adding npm deps)
// ---------------------------------------------------------------------------

function supabaseRequest(method, path, body, env) {
  return new Promise((resolve, reject) => {
    const url    = new URL(env.SUPABASE_URL);
    const data   = body ? JSON.stringify(body) : undefined;

    const options = {
      hostname: url.hostname,
      path:     `/rest/v1/${path}`,
      method:   method.toUpperCase(),
      headers: {
        "Content-Type":  "application/json",
        "apikey":        env.SUPABASE_SERVICE_KEY,
        "Authorization": `Bearer ${env.SUPABASE_SERVICE_KEY}`,
        "Prefer":        "return=representation",
      },
    };
    if (data) {
      options.headers["Content-Length"] = Buffer.byteLength(data);
    }

    const req = https.request(options, (res) => {
      let raw = "";
      res.on("data", (chunk) => { raw += chunk; });
      res.on("end", () => {
        try {
          resolve({ status: res.statusCode, data: JSON.parse(raw || "[]") });
        } catch (_) {
          resolve({ status: res.statusCode, data: raw });
        }
      });
    });

    req.on("error", reject);
    if (data) req.write(data);
    req.end();
  });
}

// ---------------------------------------------------------------------------
// Debate summary helper
// ---------------------------------------------------------------------------

function addDebateSummary(proposals) {
  return (proposals || []).map((p) => {
    const log = p.debate_log || [];
    return {
      ...p,
      debate_for:     log.filter((d) => d.stance === "FOR").length,
      debate_against: log.filter((d) => d.stance === "AGAINST").length,
      debate_abstain: log.filter((d) => d.stance === "ABSTAIN").length,
    };
  });
}

// ---------------------------------------------------------------------------
// Handler
// ---------------------------------------------------------------------------

module.exports = async function handler(req, res) {
  // CORS headers for dashboard SPA
  res.setHeader("Access-Control-Allow-Origin",  "*");
  res.setHeader("Access-Control-Allow-Methods", "GET, PATCH, OPTIONS");
  res.setHeader("Access-Control-Allow-Headers", "Content-Type, Authorization");

  if (req.method === "OPTIONS") {
    return res.status(204).end();
  }

  const env = {
    SUPABASE_URL:         process.env.SUPABASE_URL,
    SUPABASE_SERVICE_KEY: process.env.SUPABASE_SERVICE_KEY,
  };

  if (!env.SUPABASE_URL || !env.SUPABASE_SERVICE_KEY) {
    return res.status(500).json({ error: "Supabase not configured" });
  }

  // ── GET /api/research ──────────────────────────────────────────────────────
  if (req.method === "GET") {
    const { id, status, limit = "20", min_relevance = "0" } = req.query || {};

    // Single proposal by ID
    if (id) {
      const result = await supabaseRequest(
        "GET",
        `research_proposals?id=eq.${encodeURIComponent(id)}&select=*`,
        null,
        env
      );
      if (result.status !== 200 || !result.data.length) {
        return res.status(404).json({ error: "Proposal not found" });
      }
      const proposals = addDebateSummary(result.data);
      return res.status(200).json(proposals[0]);
    }

    // List proposals with optional filters
    const lim     = Math.min(parseInt(limit, 10) || 20, 100);
    const minRel  = parseInt(min_relevance, 10) || 0;
    let   qs      = `research_proposals?select=id,title,source,url,relevance,cost_impact,impacted_agents,status,pr_url,created_at,debate_log,metadata`;
    qs           += `&order=relevance.desc,created_at.desc`;
    qs           += `&limit=${lim}`;
    if (status)  qs += `&status=eq.${encodeURIComponent(status)}`;
    if (minRel > 0) qs += `&relevance=gte.${minRel}`;

    const result = await supabaseRequest("GET", qs, null, env);
    if (result.status !== 200) {
      return res.status(result.status).json({
        error: "Supabase query failed",
        detail: result.data,
      });
    }
    const proposals = addDebateSummary(result.data);
    return res.status(200).json({
      proposals,
      count: proposals.length,
      filters: { status: status || null, min_relevance: minRel, limit: lim },
    });
  }

  // ── PATCH /api/research  { id, status } ───────────────────────────────────
  if (req.method === "PATCH") {
    let body = req.body;
    if (typeof body === "string") {
      try { body = JSON.parse(body); } catch (_) { body = {}; }
    }

    const { id, status: newStatus } = body || {};

    if (!id) {
      return res.status(400).json({ error: "Missing field: id" });
    }
    const allowedStatuses = ["approved", "rejected", "implemented", "pending"];
    if (newStatus && !allowedStatuses.includes(newStatus)) {
      return res.status(400).json({
        error: `Invalid status. Allowed: ${allowedStatuses.join(", ")}`,
      });
    }

    const updatePayload = {};
    if (newStatus) updatePayload.status = newStatus;

    if (!Object.keys(updatePayload).length) {
      return res.status(400).json({ error: "Nothing to update" });
    }

    const result = await supabaseRequest(
      "PATCH",
      `research_proposals?id=eq.${encodeURIComponent(id)}`,
      updatePayload,
      env
    );

    if (result.status >= 400) {
      return res.status(result.status).json({
        error: "Update failed",
        detail: result.data,
      });
    }

    // Return a helpful message guiding the user to run the Python approval
    const responseBody = {
      updated: id,
      status:  newStatus,
    };
    if (newStatus === "approved") {
      responseBody.next_step =
        `Run: python governance/research_agent.py --approve ${id}`;
    }

    return res.status(200).json(responseBody);
  }

  // ── Method not allowed ─────────────────────────────────────────────────────
  return res.status(405).json({ error: "Method not allowed" });
};
