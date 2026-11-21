// Render smoke tests — no extra deps: react-dom/server ships with react-dom.
// Guards the Governance research panel against shape changes in
// api/main.py::_transform_research() (see tests/test_transform_research.py).
import React from "react";
import { renderToString } from "react-dom/server";
import App, { GovernanceResearchTab, HoldingDetail } from "./App";

// Exact shape _transform_research() returns today.
const FULL = {
  id: "p1", type: "whitepaper", date: "2026-11-09", source: "arxiv_api",
  title: "Temporal decay for news sentiment", relevance: 82,
  summary: "Paper abstract", proposedChange: "Add half-life decay",
  impactedAgents: ["sentiment"], costImpact: "low", debateStatus: "pending",
  status: "pending", votes: { for: 2, against: 1, abstain: 0 },
  debateLog: [{ stance: "FOR", agent: "sentiment", argument: "x" }],
  prUrl: null, url: "https://arxiv.org/abs/1", tag: "Signal Quality",
  createdAt: "2026-11-09T02:00:00+00:00",
  authors: ["Alice", "Bob", "Carol", "Dan"], venue: "NeurIPS",
  publishedAt: "2026-11-01", relevanceReason: "Targets headline decay",
  expectedImprovement: "+3% hit rate", fileToChange: "agents/sentiment.py",
  effortHours: 6, requiresPaidData: true,
};

// A proposal saved before metadata existed — new fields at their defaults.
const LEGACY = {
  ...FULL, id: "p0", tag: "Governance", authors: [], venue: "", publishedAt: "",
  relevanceReason: "", expectedImprovement: "", fileToChange: "",
  effortHours: null, requiresPaidData: false,
};

const renderTab = (feed) => renderToString(
  <GovernanceResearchTab onOpenARIA={() => {}} researchFeed={feed}
                         apiLoaded={true} liveAttribution={[]} />
);

test("App root renders without crashing", () => {
  expect(() => renderToString(<App />)).not.toThrow();
});

test("research detail panel shows all transformed fields", () => {
  const html = renderTab([FULL]);
  expect(html).toContain("Signal Quality");
  expect(html).toContain("Alice, Bob, Carol et al.");
  expect(html).toContain("NeurIPS");
  expect(html).toContain("Targets headline decay");
  expect(html).toContain("agents/sentiment.py");
  expect(html).toContain("~6h");
  expect(html).toContain("Requires paid data");
  expect(html).toContain("+3% hit rate");
});

test("legacy proposal without metadata renders and hides empty sections", () => {
  const html = renderTab([LEGACY]);
  expect(html).toContain("Governance");
  expect(html).not.toContain("Why relevant");
  expect(html).not.toContain("File to Change");
  expect(html).not.toContain("Requires paid data");
  expect(html).not.toContain("Expected improvement");
});

test("empty feed and every tag colour render", () => {
  expect(() => renderTab([])).not.toThrow();
  for (const tag of ["Architecture", "Signal Quality", "Governance", "Performance"]) {
    expect(renderTab([{ ...FULL, tag }])).toContain(tag);
  }
});


// WP-021: the 06:00 holdings review on the Portfolio tab's holding detail.
const HOLDING = {
  id: "h1", symbol: "TITAN", sector: "Consumer", qty: 10, avgBuy: 3000, currentPrice: 3300,
  targetPrice: 3900, stoplossPrice: 2800, exitSignal: "HOLD", exitReason: "within plan",
  dangerDropPct: 0, dangerConfidence: 0,
};
const NUMS = { cost: 30000, value: 33000, price: 3300, toStop: 15.2, toTarget: 18.2 };
const renderHolding = (h) => renderToString(
  <HoldingDetail h={h} n={NUMS} rec={null} onAsk={() => {}} onViewRec={() => {}} narrow={false} />
);

test("holding detail shows the morning review and thesis status", () => {
  const html = renderHolding({ ...HOLDING, reviewAction: "REVIEW", thesisStatus: "WEAKENED",
    reviewReason: "thesis weakened — upside to fair value now 8%", reviewDate: "2026-11-22" });
  expect(html).toContain("Thesis");
  expect(html).toContain("Weakened");
  expect(html).toContain("Morning review");
  expect(html).toContain("Review — thesis weakened");
});

test("holding detail without a review renders as before", () => {
  const html = renderHolding(HOLDING);
  expect(html).not.toContain("Morning review");
  expect(html).not.toContain(">Thesis<");
});
