Session 78 — Phase 2: Entity-Native Question Generation + Wikidata Resolution Quality

## Context from Sessions 76-77

Session 76 shipped Phase 1 of the entity-first architecture: voice captures about novel topics now produce FSRS-scheduled `knowledge_entities` rows. Session 77 ran the observation phase, found 4 bugs + 3 gaps, and shipped fixes for the small ones. Two bugs deferred to this session as Priority 2.

Read first:
- `research/entity-first-architecture.md` — 5-phase migration plan
- `research/session-77-observations.md` — full observation report with code references
- `memory/session77_phase1_fixes.md` — what shipped and why
- `memory/session76_entity_first_architecture.md` — Phase 1 design + validation

Current state:
- 11 `knowledge_entities` rows (5 from Karl XII capture + 6 from Viking Paris). All have `cached_question` with `rich_answer`, `memory_hook`, and (after Session 77 backfill) 6 `follow_up_queries`.
- 6 of 11 have valid Wikidata QIDs (Battle of Narva Q155726, Battle of Poltava Q152486, Abbo Cernuus Q2622892, Sigfred Q67077076, Rollo Q273773, plus Count Odo erroneously linked to Q67389525 — see Bug 4 below).
- Karl XII graded once (`knew`), rescheduled +26.9d. All other entity items due now.
- Phase 1 cleanup fixes deployed in commit `5bb9e88` — entity_type now classified by LLM, follow-ups generated, double resolution fire eliminated, voice_transcripts.node_title cleaned up.

## Priority 0 — Observe Cleanly (NEW captures, since fixes shipped)

The Session 77 prompt's observation message still applies — but now the question is whether the cleanup fixes hold up under fresh data:

1. **Do a fresh entity-path voice capture** about a topic outside existing curricula (NOT the same Karl XII or Viking Paris content). Verify in DB:
   - `entity_resolutions` — should have ~N rows, NOT ~2N (Bug 1 fix)
   - `voice_transcripts.node_title` — should be the actual primary entity name (Bug 2 fix)
   - `knowledge_entities.entity_type` — should be `person|battle|event|...` not the literal `"entity"` (Gap B fix)
   - `entities_mentioned` in `llm_result` — should not contain parenthetical date qualifiers (Gap C fix)
   - The new entity card's `cached_question.follow_up_queries` should have 6 entries (Gap A fix)

2. **Pick 2-3 entity cards in real review** and grade them. Note:
   - Are the follow-up chips genuinely useful or do they feel scattershot?
   - Does the entity_type label show up well in the card UI (as `domain_title`)? Or is "Person" / "Battle" cluttering the card?
   - Is the memory_hook quality consistent with the Karl XII/Viking samples, or did Session 77's cleanup affect generation?

If observation surfaces NEW issues, write them to `research/session-78-observations.md` and triage before proceeding to Phase 2.

## Priority 1 — Phase 2: Entity-Native Question Generation

The current `generate_entity_question()` (`scripts/review_engine.py:1721`) reuses `_key_fact_to_question()` with `entity_name`/`entity_desc` substituted for node_title/node_description. This produces good rich_answers and memory_hooks but ONLY uses facts the user explicitly captured. Phase 2 enriches questions using the entity graph — Wikidata properties + temporal neighbors + voice-capture co-occurrence.

**Key design constraints from Session 77 observation:**
- **Only enrich existing facts; never invent new facts from Wikidata alone.** "Books encode, system maintains" (north-star principle 8). Wikidata structural questions ("who succeeded X?") should be framed as enrichment ("you captured that X reigned from Y to Z; do you remember who succeeded?"), not standalone Wikidata trivia.
- **Scope temporal neighbors to entities the user has captured.** Querying `shared_entities` globally floods cards with unanchored noise ("meanwhile, the Abbasid Caliphate ruled from Baghdad"). Restrict context to the user's own graph (`shared_entities` JOIN `knowledge_entities` OR `knowledge_items`).

**What to build:**

1. **Wikidata property enrichment** — for entities with QIDs, pull a small set of structured properties via `limbic.amygdala.wikidata.WikidataClient`:
   - Persons: P22 (father), P25 (mother), P26 (spouse), P40 (child), P27 (citizenship), P19 (birth place), P20 (death place), P569/P570 (birth/death dates)
   - Rulers: P39 (position held), P580/P582 (start/end), P2962 (successor), P1365 (replaces)
   - Battles: P710 (participant), P276 (location), P585 (point in time), P1542 (caused by)
   - Events: P710 (participant), P31 (instance of), P585 (point in time)
   Cache the property fetch results on `knowledge_entities` (new column `wikidata_props_json`) so re-generation doesn't re-fetch.

2. **Temporal neighbors (scoped)** — find other `shared_entities` with overlapping date ranges (±50 years from this entity's `date_start`/`date_end`) WHERE the entity has a corresponding `knowledge_entities` or curriculum-linked `knowledge_items` row. Pass top 3-5 as "you've also captured…" context for the LLM.

3. **Co-occurrence in voice captures** — entities mentioned in the same `voice_transcripts.llm_result.entities_mentioned` list as this one. Surface 2-3 most-frequent co-occurring entities for "you discussed X when you talked about Y."

4. **Enriched `_key_fact_to_question`** — extend `_ENRICH_PROMPT` (`review_engine.py:1321`) to accept the enrichment context (Wikidata props + temporal neighbors + co-occurring entities). The current prompt only takes `node_description`; add a new `entity_graph_context` block. Make sure prompts ground the enrichment in captured facts — never write rich_answer text that asserts a fact the user didn't capture.

**Validation**: Pick one entity (Karl XII would be ideal IF Bug 3 is fixed, otherwise Battle of Poltava since it has Q152486). Manually run `generate_entity_question` after Phase 2 changes. Compare the enriched cached_question to the original. The new memory_hook should reference Peter the Great (co-occurrence) AND/OR Charles II of Spain dying 1700 (temporal neighbor) more confidently than the current generic anchors.

## Priority 2 — Wikidata Resolution Quality (Deferred Bugs 3 + 4)

Both bugs surfaced in Session 77 observation. They're resolver-level (limbic) work, not entity-path work.

### Bug 3 — Karl XII spelling variance

The actual person Karl XII of Sweden is **Q52934**, English label "Carl XII of Sweden". `wbsearchentities` for "Karl XII of Sweden" returns ONLY paintings (Q119811370 by Schröder, Q106357900 by Ankarcrona). The "Karl" spelling isn't an alias on Q52934.

Fix in `~/src/limbic/limbic/amygdala/wikidata.py` (or wherever the search lives):
- When initial search returns 0 candidates with `type=person` matching the type_hint, retry with regnal-name variants. Build a small lookup table:
  - Karl ↔ Carl ↔ Charles
  - Friedrich ↔ Frederick ↔ Federico
  - Wilhelm ↔ William ↔ Guillaume
  - Heinrich ↔ Henry ↔ Henri
  - Ludwig ↔ Louis ↔ Luigi
- Apply only when type_hint suggests person AND original returns no useful candidates.
- Document the table in `research/wikidata-resolution-quality.md` (new doc) so additions are visible.

Validation: `wbsearchentities("Karl XII of Sweden")` → 0 person candidates → retry as "Carl XII of Sweden" → returns Q52934. Run end-to-end against the Karl XII transcript. The `ent:karl_xii_of_sweden` row should pick up `wikidata_qid='Q52934'`.

### Bug 4 — Count Odo → Fire Brigade Museum

Resolver picked Q67389525 ("Count Ödön Széchenyi Fire Brigade Museum" in Istanbul) for "Count Odo". Scoring breakdown: `total=0.596, type=0.30, date=0.00`. Single candidate, no LLM disambiguation ran (only fires on `ambiguous` status), no date hint extracted by the prior Gemini extraction step.

Fix in `~/src/limbic/limbic/hippocampus/wikidata_resolve.py`:
- When a candidate scores `total >= threshold` BUT both `type_score < 0.5 AND date_score < 0.5`, downgrade status to `ambiguous` so the LLM disambiguation step gets a shot at it. Even if the LLM can only pick from the same single candidate, it can reject the match and return `null` → resolver leaves status as `no_match` instead of accepting a wrong link.
- Even better: when downgrading, broaden the candidate search (one extra Wikidata search pass with the type_hint added as a filter). For "Count Odo" with type_hint=person, this should surface Q61097 (Odo of France) as a candidate.

Validation: re-run resolution against Viking Paris capture. `ent:count_odo` should either resolve to Q61097 or remain `no_match`/`ambiguous` — never the Fire Brigade Museum.

After both fixes, also re-run resolution against the Karl XII / Viking Paris captures to clean up the existing wrong links. Backfill:

```python
# Pseudo-code
for cap_id in ['voice:vt_1776272899_7705', 'voice:vt_1776274698_7861']:
    delete from entity_resolutions where capture_id = cap_id
    re-run _resolve_voice_entities_background for the original entities_mentioned
```

Then verify `knowledge_entities` rows have the corrected `wikidata_qid`.

## Priority 3 — Phase 2 Stretch Items (only if 1+2 land cleanly)

1. **Entity intro cards for newly-created knowledge_entities**: existing `_insert_entity_intro_cards()` in `curriculum_db.py` briefs users on unknown entities before the first question. For entity-first items, this would mean inserting an "About Karl XII" intro before the first `ent:karl_xii_of_sweden` review. May be redundant with the rich_answer — observe first.

2. **Entity badge in the client**: Phase 1 deliberately didn't change the client. Entity items render as `type:'review'` with `domain_title='Person'` (after Gap B fix). Consider a subtle entity icon (distinct from 📖 book / 🎙 voice elicitation / 🔍 entity) — e.g., a Wikidata logo when `wikidata_qid IS NOT NULL`. Small UX polish, not load-bearing.

3. **`source_text` from voice capture in About-this-card modal**: when the user taps "About this card" on an entity item, show the original transcript snippet. The data is already in `knowledge_entities.sources[].source_text` — just needs to be exposed.

## Priority 4 — Phase 3 Preview (Do NOT Start)

Phase 3 (unified stream scoring that treats entity items as first-class alongside curriculum items by actual learning priority, not fixed `knowledge_weight=6.0`) needs real usage data. After Phase 2 lands and the user has graded ~30+ entity items in real reviews, then Phase 3's design becomes clear. Until then, the fixed weight is fine.

## Key Files

**Phase 2 targets:**
- `scripts/review_engine.py` — `_ENRICH_PROMPT` (~line 1321), `_key_fact_to_question()` (~line 1343), `generate_entity_question()` (~line 1721)
- `scripts/db.py` — add `wikidata_props_json TEXT` column to `knowledge_entities` (with migration)
- `limbic.amygdala.wikidata.WikidataClient` — already imported, has `get_entity()` for property fetching

**Priority 2 targets:**
- `~/src/limbic/limbic/amygdala/wikidata.py` — search retry with spelling variants
- `~/src/limbic/limbic/hippocampus/wikidata_resolve.py` — downgrade weak single-candidate matches to `ambiguous`

**Reference:**
- `research/entity-first-architecture.md` — full design (Phase 2 section ~lines 230-245)
- `research/session-77-observations.md` — observations + concrete code locations
- `research/wikidata-deployment-guide.md` — runbook for resolution work

## Commits to Reference

- `0f83650` — Entity-first Phase 1 core
- `c975c7d` — Entity path fallback widened (no node_assessments → entity)
- `a8b27b6` — Write-lock discipline in pregen
- `5bb9e88` — Session 77 cleanup fixes (double resolution, node_title, entity_types, canonical names, follow-ups)

## North Star

Phase 2 is the test of whether entity-first questions can be as RICH as curriculum-first questions. If the enrichment context (Wikidata graph + temporal neighbors + co-occurrence) produces memory hooks that anchor new facts to things the user already knows — even WITHOUT a curated curriculum — then entity-first is ready to become the default, not a fallback. If Phase 2 produces shallow questions, we need to rethink.

The Session 77 observation showed that even Phase 1 (just captured facts + LLM enrichment, no entity graph context) produced excellent memory hooks (Voltaire's 1731 history, Napoleon 1812 parallel, Treaty of Verdun anchor). Phase 2 should make those hooks more grounded in the user's actual knowledge graph rather than the LLM's general training.

**Don't skip Priority 0.** A fresh capture against the cleaned-up Phase 1 will tell us within 5 minutes whether there are more bugs to fix before Phase 2. If something is broken, fix that first.
