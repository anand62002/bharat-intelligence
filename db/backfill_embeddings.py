"""
db/backfill_embeddings.py — One-time script to generate OpenAI embeddings
for historical_events rows that are missing them.

Uses the same embedding model (text-embedding-3-small, 1536-dim) as
agents/historical_rag.py so the vectors are directly compatible.

Usage:
    python -m db.backfill_embeddings              # dry-run: show counts
    python -m db.backfill_embeddings --run         # actually generate + store
    python -m db.backfill_embeddings --run --batch 10   # custom batch size
    python -m db.backfill_embeddings --run --limit 20   # stop after 20 rows
"""

import argparse
import json
import logging
import os
import sys
import time
from datetime import datetime
from typing import Optional
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from dotenv import find_dotenv, load_dotenv

load_dotenv(find_dotenv())

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
log = logging.getLogger(__name__)

_EMBED_MODEL = "text-embedding-3-small"
_EMBED_DIM   = 1536
_RATE_LIMIT_DELAY = 0.2   # seconds between API calls (5 req/s to be safe)


# ──────────────────────────────────────────────────────────────────────────────
# OpenAI embedding
# ──────────────────────────────────────────────────────────────────────────────

def _embed_openai(text: str, api_key: str) -> Optional[list[float]]:
    """Call OpenAI embeddings API; return 1536-dim vector or None on failure."""
    payload = json.dumps({
        "model": _EMBED_MODEL,
        "input": text[:8000],
    }).encode()
    req = Request(
        "https://api.openai.com/v1/embeddings",
        data=payload,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type":  "application/json",
        },
        method="POST",
    )
    try:
        with urlopen(req, timeout=20) as resp:
            data = json.loads(resp.read().decode())
        vec = data["data"][0]["embedding"]
        if len(vec) != _EMBED_DIM:
            log.warning("Unexpected embedding dim: %d (expected %d)", len(vec), _EMBED_DIM)
        return vec
    except HTTPError as exc:
        log.warning("OpenAI HTTP error %d: %s", exc.code, exc.reason)
    except (URLError, KeyError, json.JSONDecodeError) as exc:
        log.warning("OpenAI embedding failed: %s", exc)
    return None


def _build_embedding_text(row: dict) -> str:
    """
    Construct the text to embed for a historical_events row.
    Mirrors the same text construction used in historical_rag.py
    so embeddings are semantically consistent.
    """
    parts = []
    if row.get("event_type"):
        parts.append(f"Event type: {row['event_type']}")
    if row.get("description"):
        parts.append(row["description"])
    if row.get("market_impact"):
        parts.append(f"Market impact: {row['market_impact']}")
    if row.get("outcome"):
        parts.append(f"Outcome: {row['outcome']}")
    if row.get("affected_sectors"):
        secs = row["affected_sectors"]
        if isinstance(secs, list):
            secs = ", ".join(str(s) for s in secs)
        parts.append(f"Sectors: {secs}")
    return " | ".join(parts) if parts else (row.get("description") or "")


# ──────────────────────────────────────────────────────────────────────────────
# Supabase helpers
# ──────────────────────────────────────────────────────────────────────────────

def _get_supabase_client():
    """Return an initialised Supabase client or raise RuntimeError."""
    url = os.environ.get("SUPABASE_URL")
    key = os.environ.get("SUPABASE_SERVICE_KEY")
    if not url or not key:
        raise RuntimeError(
            "SUPABASE_URL and SUPABASE_SERVICE_KEY must be set in environment"
        )
    from supabase import create_client
    return create_client(url, key)


def _fetch_rows_missing_embeddings(client, limit: int = 200) -> list[dict]:
    """
    Pull rows from historical_events where embedding IS NULL.
    Returns list of {id, event_type, description, market_impact, outcome, affected_sectors}.
    """
    resp = (
        client.table("historical_events")
        .select("id, event_type, description, market_impact, outcome, affected_sectors, event_date")
        .is_("embedding", "null")
        .order("id")
        .limit(limit)
        .execute()
    )
    return resp.data or []


def _count_rows(client) -> dict:
    """Return {total, with_embedding, without_embedding} counts."""
    # Total
    total_resp = (
        client.table("historical_events")
        .select("id", count="exact")
        .execute()
    )
    total = total_resp.count or 0

    # With embedding
    has_resp = (
        client.table("historical_events")
        .select("id", count="exact")
        .not_.is_("embedding", "null")
        .execute()
    )
    has_embedding = has_resp.count or 0

    return {
        "total":           total,
        "with_embedding":  has_embedding,
        "without_embedding": total - has_embedding,
    }


def _store_embedding(client, row_id: int, embedding: list[float]) -> bool:
    """Update a single row's embedding. Returns True on success."""
    try:
        client.table("historical_events").update(
            {"embedding": embedding}
        ).eq("id", row_id).execute()
        return True
    except Exception as exc:
        log.error("Failed to store embedding for id=%d: %s", row_id, exc)
        return False


# ──────────────────────────────────────────────────────────────────────────────
# Main
# ──────────────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(description="Backfill OpenAI embeddings for historical_events")
    parser.add_argument("--run",   action="store_true", help="Actually generate and store embeddings (default: dry-run only)")
    parser.add_argument("--batch", type=int, default=20, help="Rows per Supabase fetch batch (default: 20)")
    parser.add_argument("--limit", type=int, default=0,  help="Stop after N rows total (0 = no limit)")
    args = parser.parse_args()

    # ── Pre-flight checks ─────────────────────────────────────────────────────
    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        log.error("OPENAI_API_KEY not set in environment. Cannot generate embeddings.")
        sys.exit(1)

    try:
        db = _get_supabase_client()
    except RuntimeError as exc:
        log.error(str(exc))
        sys.exit(1)

    # ── Count rows ────────────────────────────────────────────────────────────
    counts = _count_rows(db)
    log.info(
        "historical_events: total=%d  with_embedding=%d  missing=%d",
        counts["total"], counts["with_embedding"], counts["without_embedding"],
    )

    if counts["without_embedding"] == 0:
        log.info("All rows already have embeddings. Nothing to do.")
        return

    if not args.run:
        log.info(
            "DRY RUN — %d rows need embeddings. Re-run with --run to generate them.",
            counts["without_embedding"],
        )
        return

    # ── Estimate cost ─────────────────────────────────────────────────────────
    # text-embedding-3-small: $0.02 per 1M tokens; avg ~80 tokens per event row
    est_tokens = counts["without_embedding"] * 80
    est_cost   = est_tokens / 1_000_000 * 0.02
    log.info(
        "Estimated cost: ~%.4f USD (%d rows × ~80 tokens @ $0.02/1M)",
        est_cost, counts["without_embedding"],
    )

    # ── Process in batches ────────────────────────────────────────────────────
    processed  = 0
    succeeded  = 0
    failed     = 0
    start_time = datetime.now()

    batch_size = min(args.batch, 50)   # cap at 50 per fetch to avoid large payloads
    max_rows   = args.limit if args.limit > 0 else 10_000

    while processed < max_rows:
        fetch_n = min(batch_size, max_rows - processed)
        rows    = _fetch_rows_missing_embeddings(db, limit=fetch_n)
        if not rows:
            log.info("No more rows missing embeddings.")
            break

        for row in rows:
            if processed >= max_rows:
                break

            row_id = row["id"]
            text   = _build_embedding_text(row)
            if not text.strip():
                log.warning("Row id=%d has no text to embed — skipping", row_id)
                processed += 1
                failed    += 1
                continue

            embedding = _embed_openai(text, api_key)
            if embedding is None:
                log.error("Failed to embed row id=%d", row_id)
                processed += 1
                failed    += 1
                # Brief pause after failure before continuing
                time.sleep(1.0)
                continue

            ok = _store_embedding(db, row_id, embedding)
            processed += 1
            if ok:
                succeeded += 1
                log.info(
                    "[%s/%s] id=%s embedded OK (dim=%s)",
                    processed, min(counts["without_embedding"], max_rows),
                    row_id, len(embedding),
                )
            else:
                failed += 1

            # Rate-limit guard
            time.sleep(_RATE_LIMIT_DELAY)

    elapsed = (datetime.now() - start_time).total_seconds()
    log.info(
        "Done in %.1fs — processed=%d succeeded=%d failed=%d",
        elapsed, processed, succeeded, failed,
    )
    if failed:
        log.warning("%d rows failed. Re-run to retry.", failed)
    else:
        log.info("All embeddings generated successfully.")

    # ── Final counts ──────────────────────────────────────────────────────────
    final_counts = _count_rows(db)
    log.info(
        "Final: total=%d  with_embedding=%d  missing=%d",
        final_counts["total"], final_counts["with_embedding"],
        final_counts["without_embedding"],
    )


if __name__ == "__main__":
    main()
