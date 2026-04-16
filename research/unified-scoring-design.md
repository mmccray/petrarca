# Unified Scoring Design — Entity Weight Tuning

**Date**: April 16, 2026 (Session 79)
**Status**: Design doc — not yet implemented
**Depends on**: Synchronic card E5 data, entity review volume, stream quality audit findings

## Current State

### Three scoring systems

1. **Knowledge items** (`generate_review_stream()` in `curriculum_db.py:1219`):
   - Base: `knowledge_weight` from knowledge state — engaged=8.0, mentioned=4.0, anchored=3.0
   - Boost: +2.0 for factual types (date/event/person), -1.0 for abstract (significance/connection)
   - Scheduling: overdue items get +10.0 + min(overdue_days*0.3, 5.0), never-reviewed get +2.0
   - Penalty: gap-fill items get -5.0 (capped at 3/batch)
   - Origin hierarchy: book_whole > book_chapter > gap_fill

2. **Entity items** (`curriculum_db.py:1325`):
   - Fixed: `knowledge_weight=6.0` for all entities regardless of type/state
   - Same scheduling formula as knowledge items (overdue/never-reviewed boosts)
   - Same factual type boost (+2.0 for date/event/person)
   - No gap-fill penalty (entities are always voice-captured)

3. **Structural cards** (`_mix_structural_cards()`):
   - Not scored — selected by activation gate + domain diversity, inserted at fixed positions (every 3rd slot)
   - Aspect: ≥5 KI gate, up to 3/batch
   - Sequence: ≥5 KI + ≥3 reviewed aspect positions, up to 2/batch
   - Synchronic: ≥5 KI gate, up to 2/batch

### Stream audit findings (Session 79)

| Metric | Value | Notes |
|--------|-------|-------|
| ML cards due | 564 | All never-reviewed, infinite backlog |
| KI with cached_question | 96 | 13 never-reviewed, 14 overdue |
| Entity items | 11 | 10 never-reviewed |
| Structural cards | 550 | 6/2284 positions reviewed |
| Typical 10-card session | 10 review, 3 aspect, 3 ML, 3 ML quiz, 2 synchronic, 1 sequence, 1 entity_intro | 23 total items after interleaving |

**Key observation**: The stream is NOT ML-dominated. Review items take 50% of slots. Structural cards get 26% (6/23). ML gets 13% (3/23). The infinite ML backlog doesn't flood the stream because interleaving ratios cap insertion.

## Design Questions for Entity Weight Tuning

### Q1: Should entity weight vary by entity_type?

**Current**: All entities at 6.0 (between engaged=8.0 and mentioned=4.0).

**Options**:
- Persons at 7.0, events at 5.0 (persons are the primary scaffold per design principle #7)
- Battle events at 6.0, biographical events at 4.0 (battles are more testable)
- All equal (current) — entity_type was just backfilled, data insufficient to differentiate

**Recommendation**: Keep uniform for now. With only 11 entities, type-based weighting won't be noticeable. Revisit when entity count reaches 50+.

### Q2: Should Wikidata nexus_score influence weight?

**Rationale**: Well-connected entities (Frederick II nexus_score=high) are more valuable as scaffold nodes than obscure figures. They appear in more cross-domain connections.

**Risk**: Over-weighting high-nexus entities creates a rich-get-richer dynamic. Minor figures that the user specifically captured via voice may be MORE important to review (they're novel to the user) despite low nexus_score.

**Recommendation**: Don't use nexus_score for review priority. It's already used in synchronic card generation (selecting the most notable contemporaries). For review, the user's capture intent matters more than Wikipedia connectivity.

### Q3: Should voice-capture recency boost entity weight?

**Rationale**: Fresh captures represent active learning — the user just talked about this topic. A 48h boost would prioritize recent voice captures for initial encoding.

**Implementation**: `knowledge_weight = 6.0 + max(0, 3.0 - age_hours / 16)` — 3.0 boost at capture time, linearly decaying to 0 over 48h.

**Risk**: With only 2-3 voice captures per week, this boost would rarely fire. But when it does, it aligns with the "books encode, system maintains" principle (#8).

**Recommendation**: Implement this. It's a low-risk change (small boost, short window) that reinforces the voice capture → review loop.

### Q4: Should review_count decay the weight?

**Rationale**: Never-reviewed entities need more exposure. Entities reviewed 5+ times are in the FSRS scheduling loop and don't need the initial boost.

**Current**: Never-reviewed entities get +2.0 (same as knowledge items). After first review, FSRS scheduling takes over.

**Recommendation**: No change needed. The existing never-reviewed boost + FSRS is sufficient.

## Proposed Changes (Phase 3)

1. **Voice-capture recency boost**: +3.0 linearly decaying over 48h on entity items
2. **No other changes** until entity count reaches 50+ and we have E5 data

## ML Backlog Observation

The 564 ML cards (all due, all never-reviewed) are not a problem for stream quality — they're rate-limited by interleaving ratios. However, the backlog means:
- ML cards will always be available to fill empty slots
- New ML cards (from future voice captures) won't get priority over old ones
- The user never sees a "caught up" state for ML

**Possible future action**: Add a `created_at`-based freshness boost so newer ML cards appear before ancient ones. Not urgent.

## E5 Baseline Measurement (Pre-Synchronic Cards)

Scanned all 42 voice transcripts for entity mentions across domains. Results:

| Metric | Value |
|--------|-------|
| Transcripts analyzed | 41 (with entity mentions) |
| Transcripts with cross-domain mentions | 35 (85%) |
| Same-domain entity mentions | 158 |
| Cross-domain entity mentions | 223 |
| Cross-domain ratio | 58.5% |

**Top cross-domain pairs** (by mention frequency):
1. Sicily → Byzantine (13)
2. Sicily → Ancient Greece (12)
3. Sicily → Literature (11)
4. Byzantine → Philosophy (10)
5. Sicily → Architecture (10)

**Caveat**: Many "cross-domain" mentions are shared geographic entities (Rome, Italy, Sicily) that belong to multiple curricula by nature. The 58.5% baseline is inflated by these. A more meaningful E5 metric would track **novel cross-domain connections** — entity pairs from different domains that the user mentions together for the first time after seeing a synchronic card featuring both.

**Proposed E5 success criteria**: After 2-4 weeks of synchronic card reviews, check whether:
1. New cross-domain pairs appear in voice transcripts that match synchronic card content
2. The user spontaneously mentions temporal contemporaries (e.g., "while Frederick II was in Sicily, Averroes was in Al-Andalus")
3. Cross-domain mention density increases in domains where synchronic cards are strongest

## Entity Intro Card Observation

6 entity_intro cards appeared in the 40-item stream — one for each unreviewed entity with a description. These are one-shot "here's what we know about X" cards that don't require grading. After the entity is reviewed once, the intro stops appearing.

With 10 unreviewed entities, intros take ~12% of stream slots. This is fine for now but could crowd the stream if entity count grows. Consider: show at most 2 entity_intros per batch.
