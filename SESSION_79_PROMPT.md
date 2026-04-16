# Session 79 — Synchronic Cards + Entity Consolidation

## Context from Sessions 71-78

The last 8 sessions executed a major pivot: from read-later feed to quiz-first review app (Session 71), through structural card types (Sessions 72-75: aspect + sequence), entity-first architecture (Sessions 76-77: Phase 1 + observation), and entity-graph enrichment (Session 78: Phase 2 — Wikidata properties, temporal neighbors, voice co-occurrence driving LLM memory hooks).

Session 78 validated the entire entity-enrichment pipeline end-to-end:
- Karl XII of Sweden → Q52934 resolved via regnal-name spelling retry (Bug 3, limbic commit `e7d8498`)
- Count Odo correctly downgraded from Q67389525 (Fire Brigade Museum) to `ambiguous` (Bug 4)
- Battle of Poltava's memory_hook changed from generic ("1709 famine, Great Frost") to graph-grounded ("nine years after Karl XII's victory at Narva in 1700, reversing their fortunes completely")
- Gemini date-coercion bug caught and fixed during backfill (commit `0a3a304`)

**System state as of deploy `0a3a304`:**

| Metric | Count | Note |
|---|---|---|
| knowledge_entities | 11 | 6 with QID, 1 reviewed, all entity_type='entity' (legacy) |
| knowledge_items | 266 | 96 with cached_question, 116 reviewed at least once |
| structural_cards | 540 | 523 aspect + 17 sequence, only 6 positions reviewed |
| microlearning_cards | 564 | 236 due now — stream is ML-heavy |
| shared_entities | 590 | 528 with QID (89.5%), 469 with date ranges |
| entity_resolutions | 1,368 | 570 resolved, 653 ambiguous, 117 no_match |
| curriculum_domains | 13 | 6 active (≥10 KI): Sicily, Rome, Greece, Byzantine, Islamic, Music |

Read first:
- `research/structural-review-redesign.md` — Phase 5 (Synchronic Cards) design at lines 228-248
- `research/entity-first-architecture.md` — Phase 2 just completed
- `research/session-77-observations.md` — observation methodology
- `memory/session78_phase2_enrichment.md` — what shipped and what's deferred
- `memory/feedback_temporal_hooks.md` — north-star: temporal hooks ARE the key retention mechanism

## Priority 0 — Entity Consolidation (small, high-value cleanup)

The 11 existing `knowledge_entities` rows all have `entity_type='entity'` (the literal string from before Session 77's classifier fix) and Phase 1 `cached_question` content (pre-enrichment). Both are easy batch fixes.

### 0a: Entity-type backfill

For the 6 entity items with `wikidata_qid`, query Wikidata P31 to determine real type:
- Battle of Poltava (Q152486) → P31=Q178561 (battle)
- Battle of Narva (Q155726) → P31=Q178561 (battle)
- Karl XII of Sweden (Q52934) → P31=Q5 (person)
- Abbo of Saint-Germain (Q2622892) → P31=Q5 (person)
- Rollo (Q273773) → P31=Q5 (person)
- Sigfred (Q67077076) → P31=Q5 (person)

For the 5 without QID, infer from the entity name (these are clearly battles, events, persons):
- Count Odo → person
- Emperor Charles the Fat → person
- Great Northern War (1700 attack) → event
- Russian Campaign (1708-1709) → event
- Viking siege of Paris (885-886) → event

Write a small server-side script that backfills `entity_type` on these 11 rows. This fixes:
- `domain_title` on cards (shows "Person"/"Battle"/"Event" instead of "Entity")
- `_fetch_wikidata_props` uses the correct per-type property set instead of union-of-all
- The entity badge now renders contextually (person = 👤, battle = ⚔️, etc. — if we want)

### 0b: Batch-regenerate cached_questions with Phase 2 enrichment

All 11 entity items have Phase 1 cached_questions (no entity-graph context). Null them out and call `generate_entity_question()` for each. Compare old vs new memory_hooks in a summary report.

Validation: after regen, every entity item's memory_hook should reference at least one of:
- A scoped temporal neighbor (e.g., "nine years after Narva")
- A voice co-occurring entity (e.g., "Karl XII" when grading Poltava)
- A Wikidata structural property (e.g., "succeeded by Ulrika Eleonora")

If some hooks are still generic (no entity-graph anchors), diagnose: missing dates on shared_entities? No co-occurrence because only 2 transcripts? No Wikidata props because no QID?

### 0c: Fresh observation

Do ONE new voice capture about a topic outside existing curricula + Karl XII + Viking Paris — verify the full pipeline fires correctly:
- `entity_types` map in LLM output (Session 77 fix)
- Wikidata resolution with regnal-variant retry (Session 78 fix)
- `wikidata_props_json` populated on new entity items (Phase 2)
- Follow-up queries generated (Session 77 Gap A fix)
- Entity-graph context block in enrichment prompt (Phase 2)

Only proceed to Priority 1 if observation is clean. If new bugs surface, write to `research/session-79-observations.md` and triage before building.

## Priority 1 — Synchronic Cards (Phase 5 of structural-review-redesign)

This is the session's main build target. Synchronic cards are the most novel card type in the design doc and the most aligned with the north-star "temporal hooks" principle. They test cross-domain awareness: "What was happening in Sicily when Karl XII fought at Poltava?"

### Why now

Three preconditions are now met:
1. **Wikidata temporal data**: 469 of 590 shared_entities have `date_start`/`date_end`. Entity resolution is at 89.5%. We can query "who was alive/ruling in year X?"
2. **Entity-first architecture**: `knowledge_entities` + `knowledge_items` provide the "user's own graph" — we only show synchronic connections to entities the user has captured or studied.
3. **Structural card infrastructure proven**: `structural_cards` + `structural_positions` tables, FSRS per-position scheduling, AspectCard/SequenceCard components — all working. SynchronicCard follows the same pattern.

### What the design doc says (lines 228-248)

```
┌─────────────────────────────────────────┐
│ The World in 1066                        │
│ When William conquers England            │
│                                          │
│  England     William the Conqueror       │
│  Sicily      ???                [Reveal] │
│  Papacy      ???                [Reveal] │
│  Byzantium   Constantine X               │
│  HRE         Henry IV                    │
│  Caliphate   al-Qa'im (Baghdad)          │
└─────────────────────────────────────────┘
```

- **Presentation**: Schematic text list with region labels, NOT a map
- **Blanks**: 2-3 per card, FSRS-scheduled per position (same as sequence cards)
- **Anchors**: Well-known positions shown as context; due/weak ones blanked
- **Activation gate**: Only show synchronic cards when the anchor event is well-established (user has reviewed the anchor entity at least once)
- **Scope**: Only cross-domain connections where the user has studied BOTH domains. Don't show China alongside Sicily unless the user has a China curriculum.

### What to build

**1. Synchronic card generation** (`scripts/generate_synchronic_cards.py`)

For each well-established temporal anchor (a `knowledge_item` or `knowledge_entity` with `review_count >= 2` and `stability_days >= 3.0`), find entities from OTHER domains active at the same time:

```sql
-- Find contemporaries of an anchor entity
SELECT se.entity_id, se.name, se.entity_type, se.date_start, se.date_end,
       se.description, ecl.domain_id
FROM shared_entities se
JOIN entity_curriculum_links ecl ON ecl.entity_id = se.entity_id
WHERE se.date_start <= :anchor_year
  AND se.date_end >= :anchor_year
  AND ecl.domain_id != :anchor_domain
  AND ecl.domain_id IN (SELECT DISTINCT curriculum_domain
                         FROM knowledge_items
                         WHERE review_count > 0)
ORDER BY se.nexus_score DESC
LIMIT 8
```

Group results by domain. Pick 5-8 positions across 3-5 domains. Generate a title ("The World in {year}") and a subtitle ("When {anchor_event}"). The anchor entity is always shown (not blanked).

For persons who span decades, use the most notable event in that year if available (from key_facts), or just their reign/role.

**Generation uses Gemini Flash** (per LLM calling discipline — user-facing, low latency). Prompt:

```
Given these historical figures/events active around {year}, generate a synchronic card.

Anchor: {anchor_name} ({anchor_dates}) — {anchor_description}

Contemporaries by domain:
{domain}: {entity1} ({dates}) — {desc}
...

For each position, write:
- A short label (the entity's most notable role/title at this time)
- A question framing (for when this position is blanked)
- Why this connection matters (1 sentence — what links these domains at this moment)

Return JSON: {"title": "...", "subtitle": "...", "positions": [...]}
```

**Key design constraint**: "Only show cross-domain where there's an actual relationship" (resolved decision #3 from the design doc). Don't include random contemporaries — include only entities the LLM can articulate a connection for. The LLM should drop positions where the connection is purely coincidental ("they happened to live at the same time").

**2. `SynchronicCard.tsx` component**

Similar to SequenceCard but layout is GEOGRAPHIC (rows by domain) not TEMPORAL (timeline). Each row:
```
  Domain Label     Entity Name / ???     [Reveal]
```

Known positions show the entity name. Due/weak positions are blanked with a question prompt. Tap reveals + shows the 1-sentence "why this matters" connection text.

Binary grading per position (knew/missed), same as aspect/sequence.

**3. Schema + FSRS integration**

Synchronic cards go in the existing `structural_cards` table with `card_type='synchronic'`. Positions go in `structural_positions`. FSRS scheduling is identical to aspect/sequence.

**4. Stream integration**

Add to `_mix_structural_cards()` in `curriculum_db.py`. Activation gate: the ANCHOR entity must have been reviewed at least once (not just `review_count >= 0` like aspect cards — synchronic cards are higher-order and need the anchor to be established).

### Scope control

Start with **5-10 synchronic cards** generated from the user's strongest temporal anchors across the 6 active domains. This is enough to validate E5 (do synchronic cards create cross-domain connections?) without flooding the stream.

**Don't generate for every possible year.** Pick years where:
- The anchor is a well-reviewed entity (stability ≥ 3d)
- At least 3 domains have entities active at that time
- The user has studied at least 2 of those domains

### Validation

After generating + reviewing 3-5 synchronic cards:
- Do the "why this matters" connection texts feel insightful or obvious?
- Does the geographic layout (domain rows) scan quickly?
- Is the grading flow (reveal/knew/missed per position) as fast as sequence cards?
- Did any synchronic card create a "wow, I didn't realize X and Y were contemporaries" moment? (This is the E5 hypothesis.)

## Priority 2 — Stream Quality Audit

The review stream currently shows 236 ML cards due, ~10 entity items at fixed weight 6.0, and structural cards gated by activation. Two issues to investigate:

### 2a: ML card dominance

564 total ML cards vs 266 knowledge items vs 540 structural cards. The stream may be ML-heavy. Check:
- What fraction of a typical 10-card session is ML vs review vs structural?
- Are high-quality review items (book-sourced, recent voice captures) getting buried under ML cards?
- Should ML card weight be reduced (currently interleaved at 1:3 for voice_wondering/correction, 1:7 for follow_up)?

### 2b: Structural card activation

523 aspect cards exist across 7 domains, but only 6 positions have been reviewed. This suggests structural cards aren't appearing frequently enough. Check:
- How many domains pass the ≥5 KI activation gate?
- Is the domain-diverse selection (`ROW_NUMBER() OVER (PARTITION BY domain_id)`) working?
- Are structural cards showing up in real review sessions, or are they pushed to the end by higher-scoring items?

If structural cards are being starved, consider:
- Reserving 2-3 slots per session for structural cards (hard quota, not just score-based)
- Reducing the activation threshold from ≥5 KI to ≥3 KI for domains the user has actively reviewed

### 2c: Entity item weight tuning

Entity items are at fixed `knowledge_weight=6.0`. Phase 3 design question: should this be dynamic based on:
- Entity type (persons more important than events?)
- Wikidata nexus_score (well-connected entities more important?)
- Voice-capture recency (fresh captures boosted for 48h, per Phase 8 spec?)
- Review_count (never-reviewed items boosted, repeatedly-reviewed items at normal priority?)

Don't implement Phase 3 yet — just document the design in a short `research/unified-scoring-design.md`. The data from Priority 1 synchronic cards + ongoing entity reviews will inform the actual algorithm.

## Priority 3 — Stretch (only if 0-2 land cleanly)

### 3a: Ambiguous resolution triage

653 entity_resolutions with status='ambiguous' (48% of all resolutions). Many of these are from the backfill batch (Session 70) with weak context. A manual triage pass on the top-50 most-mentioned ambiguous entities could:
- Resolve obvious cases (where one candidate is clearly correct but scored just below threshold)
- Mark truly ambiguous cases for future LLM disambiguation with better context
- Identify patterns for resolver improvement (which type_hints consistently fail?)

Check `/admin/entity-queue` — does the admin UI show these? If so, surface a "most impactful ambiguous" view (sorted by frequency of mention across transcripts).

### 3b: Entity date coverage audit

For Phase 5 synchronic cards, every entity needs `date_start`/`date_end`. Current coverage: 469/590 (79.5%). The 121 missing dates are likely:
- Places (permanent — set date_start=-3000, date_end=2026?)
- Concepts (atemporal — exclude from synchronic queries)
- Events with missing P585/P580/P582 claims on Wikidata

Audit the 121 missing-date entities. For the subset that are persons or events with Wikidata QIDs, backfill dates from P569/P570/P585/P580/P582. For places, decide whether to include them in synchronic cards (probably not — "Paris existed in 1066" isn't useful).

### 3c: Experiment E5 baseline

Before the user starts reviewing synchronic cards, measure the baseline for E5: do voice elicitations already include cross-domain mentions? Scan all 42 voice_transcripts for mentions of entities from domains OTHER than the transcript's primary domain. This gives us the "before" measurement for whether synchronic cards increase cross-domain recall.

## Key Files

**Synchronic card generation:**
- `scripts/generate_synchronic_cards.py` (NEW — model after `generate_aspect_cards.py` and `generate_sequence_cards.py`)
- `scripts/curriculum_db.py` — `_mix_structural_cards()` for stream integration
- `scripts/db.py` — existing `structural_cards` + `structural_positions` tables (no schema changes needed — `card_type='synchronic'`)

**SynchronicCard component:**
- `app/components/SynchronicCard.tsx` (NEW — model after `SequenceCard.tsx`)
- `app/app/(tabs)/index.tsx` — add rendering branch for `type:'synchronic'`

**Entity consolidation:**
- `scripts/review_engine.py` — `generate_entity_question()`, `_fetch_wikidata_props()`
- `scripts/db.py` — `knowledge_entities` schema (existing)

**Design references:**
- `research/structural-review-redesign.md` § Phase 5
- `research/entity-first-architecture.md` § Phase 2 (just completed)
- `memory/feedback_temporal_hooks.md` — temporal hooks priority ordering
- `memory/feedback_factual_scaffold_priority.md` — facts before concepts

## Commits to Reference

Session 78 commits:
- `e7d8498` (limbic) — Regnal spelling variants + weak-match downgrade
- `08c4551` — Phase 2 entity-graph enrichment
- `39c83b2` — Stretch UX (badge, source excerpts, entity intro cards)
- `0a3a304` — Gemini date coercion fix

Earlier structural card commits:
- Search git log for "aspect card", "sequence card", "structural" for the full chain

## North Star

Synchronic cards are where "temporal hooks" (north-star #6), "hooks not facts" (#1), and "curriculum as bridge" (#5) converge. A synchronic card showing the user that Count Roger conquered Sicily in 1091 while the First Crusade launched in 1096 isn't just trivia — it's the causal-geographic context that makes both events comprehensible. The entity-first architecture (Sessions 76-78) gave us the data layer. The structural card infrastructure (Sessions 72-75) gave us the scheduling + rendering pattern. Session 79 connects them.

The design doc's E5 hypothesis: "Users who see synchronic cards will spontaneously recall cross-domain facts more often in voice elicitations." That's the real test — not whether synchronic cards are technically working, but whether they change how the user thinks about history.
