# RAG Corpus Update Plan — Task #8

> Last updated: 2026-07-19
> Current corpus: 150 historical_events rows, all with OpenAI embeddings (seeded 2026-05-12)

---

## Current State

| Metric | Value |
|---|---|
| Total events | 150 |
| Events with embeddings | 150/150 ✅ |
| Date range | ~2019–2025 (seeded from Google News RSS) |
| Monthly auto-refresh | `db/auto_seed_rag.py` runs 1st of each month (08:15 IST) |
| Deduplication window | ±7 days per event_type vs existing rows |
| Classification | Claude Haiku (LLM) or keyword fallback |

---

## Gaps Identified

### 1. Missing event types / categories
The current corpus was seeded primarily via 8 Google News RSS queries for macro events.
Likely underrepresented:
- Sector-specific shocks (pharma USFDA import alerts, bank NPA cycles)
- Budget announcements post-2023
- FII panic episodes (US Fed rate decisions, China/HK selloffs)
- Commodity super-cycle events (crude >$100 impacts on India)
- Promoter scandal / corporate governance crises

### 2. Date staleness
Events from July 2025 onwards are missing (knowledge cutoff). The auto-seeder covers
the most recent 35 days via RSS, so ongoing events are being added. But the 2025 corpus
is sparse.

### 3. No earnings surprise events
The RAG corpus contains macro events but no individual stock earnings surprises
(large beat/miss events that caused sector repricing). These are the most valuable
analogues for the `historical_rag` agent.

---

## Action Plan

### Immediate (can run now)
```powershell
# Run a manual seed cycle with extended lookback (90 days instead of 35)
python -m db.auto_seed_rag --run --days 90 --max 50

# Check current coverage
python -m db.auto_seed_rag  # dry run, prints stats
```

### Short-term (add new event type queries)
Edit `db/auto_seed_rag.py` — add these RSS query terms to `_INDIA_MACRO_QUERIES`:
```python
"India pharma USFDA import alert site:moneycontrol.com OR site:economictimes.com",
"India bank NPA crisis RBI action",
"India FII selloff Fed rate hike impact",
"Nifty crash circuit breaker trigger",
"India Budget market reaction",
"India promoter fraud corporate governance",
```

### Medium-term (earnings surprises corpus)
Create a separate seeder `db/seed_earnings_events.py` that:
1. Queries `recommendations` table for past BUY recs with large actual returns
2. Fetches earnings announcement context from BSE announcements API for those dates
3. Classifies as EARNINGS_SURPRISE_BEAT / MISS event
4. Inserts with embedding into `historical_events`

This would create a self-improving feedback loop: good past calls inform future ones.

### Embedding model
Currently using OpenAI `text-embedding-3-small` (via `OPENAI_API_KEY`).
If OpenAI key is not set, embeddings are skipped silently.
Verify: `SELECT COUNT(*) FROM historical_events WHERE embedding IS NULL`

---

## Monthly Auto-Seeder Settings

Current config in `db/auto_seed_rag.py`:
- `--days 35`: 35-day lookback window
- `--max 30`: max 30 new events per run
- Dedup: ±7-day window per event_type

Recommended change: increase `--max 50` and add the new query terms above.

---

## Verification Command

```powershell
# Check embedding coverage and date distribution
python -c "
from db.backfill_embeddings import _supabase
c = _supabase()
rows = c.table('historical_events').select('event_type,event_date,embedding').execute().data
total = len(rows)
with_emb = sum(1 for r in rows if r.get('embedding'))
print(f'Total: {total}, With embeddings: {with_emb}')
from collections import Counter
types = Counter(r['event_type'] for r in rows)
for t,n in types.most_common():
    print(f'  {t}: {n}')
"
```
