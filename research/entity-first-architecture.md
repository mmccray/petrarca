# Entity-First Architecture: Curricula as Optional Overlay

**Date**: 2026-04-14
**Status**: Design proposal — architecturally significant, needs careful phasing
**Supersedes**: Assumptions in `overlapping-curricula-vision.md` (curricula as foundational), `curriculum-system-audit.md` (§4 "What if no curricula?")
**Builds on**: `entity-profiles-design.md` (entity profiles as proto-curricula), Wikidata entity resolution (Session 70)

## The Problem

The current architecture requires **everything to route through a curriculum** before it can become reviewable knowledge. The pipeline is:

```
Voice capture → entity detection → entity_curriculum_links → curriculum node → knowledge_item
```

This means:
- A podcast about Iran can't create knowledge items (no Iran curriculum)
- A book about Norman France routes to `western_music` via a tangential entity match
- Voice captures about topics outside existing curricula fall to a weak ML card fallback
- The user must pre-generate curricula for every possible topic of interest

This is backwards. The user reads widely and captures knowledge from many sources. The system should ingest that knowledge directly, tied to the entities and events discussed, and only *optionally* organize it into curricula for gap analysis and structured review.

## The Insight from Entity Profiles

The `entity-profiles-design.md` doc (April 2026) already identified the key pattern:

> "Entity profiles are proto-curricula. When you accumulate enough entity profiles in a domain, they naturally suggest a curriculum."

And for Karl XII (no Swedish curriculum):

> "No curriculum needed. The entity profile IS the knowledge structure."

The Wikidata backfill (Session 70) gave us the infrastructure: 509/570 entities resolved to canonical QIDs, background resolution on live voice captures, external IDs (VIAF, GND, GeoNames). **The entity layer is ready to be primary. The curriculum layer isn't ready to be bypassed yet, but that's an implementation gap, not a design requirement.**

## Proposed Architecture: Entity-First

### Core Principle

**Entities (people, events, places, works) are the fundamental knowledge unit.** Each has a Wikidata QID, structured metadata, and relationships to other entities. Knowledge is tracked per entity. Curricula are an optional overlay that groups entities into bounded pedagogical units for gap analysis and structured review.

### The New Pipeline

```
Voice capture → entity extraction → Wikidata resolution → knowledge tracked per entity
                                              ↓
                                    Wikidata properties give us:
                                    - family/succession relationships
                                    - geographic coordinates
                                    - temporal proximity
                                    - occupation/type classification
                                              ↓
                                    Questions generated from:
                                    - extracted facts + entity context
                                    - related entities (temporal, geographic, relational)
                                    - cross-source connections ("you also discussed X when...")
                                              ↓
                                    Curriculum (optional):
                                    - gap analysis ("what don't I know about Sicily?")
                                    - structural cards (aspect, sequence)
                                    - progress visualization
                                    - voice elicitation topic list
```

### What Changes

| Component | Current (curriculum-first) | Proposed (entity-first) |
|-----------|---------------------------|------------------------|
| **Knowledge unit** | Curriculum node (`sicily_his:roger_ii`) | Entity (Wikidata QID `Q155124`) |
| **Knowledge tracking** | `knowledge_items` keyed by `(domain, node)` | `knowledge_entities` keyed by entity_id/QID |
| **Voice capture routing** | Entity match → curriculum link → domain → nodes | Entity extraction → QID resolution → direct knowledge tracking |
| **Question generation** | Needs curriculum context (sibling nodes, prerequisites) | Uses entity graph (related entities, temporal neighbors, captured facts) |
| **Deduplication** | One `knowledge_item` per curriculum node | One `knowledge_entity` per QID (globally unique, better than node dedup) |
| **Cross-domain connections** | Via `entity_curriculum_links` bridge table | Native: Wikidata relations, co-occurrence in captures |
| **Gap analysis** | "45/67 Sicily nodes known" | Still via curriculum overlay: "45/67 Sicily entities covered" |
| **Structural cards** | Generated from curriculum `key_facts` | Still curriculum-backed (this is where curricula earn their keep) |

### What Stays the Same

- **FSRS scheduling** — completely curriculum-agnostic today, works on any item with an ID
- **Interaction logging** — curriculum-independent
- **Voice capture transcription** — no curriculum dependency
- **Wikidata resolution** — already entity-first
- **Review card UX** — renders whatever the stream sends

## Where Curriculum Currently Earns Its Keep (Honest Assessment)

The Session 54 audit identified these as "essential" uses of curriculum. Re-evaluating each:

### 1. Review Card Deduplication
**Audit said**: Essential — without curriculum nodes, the old system produced 7 duplicate questions per concept.

**Re-evaluation**: Wikidata QIDs are actually **better** for dedup. `Q155124` (Roger II) is globally unique. Curriculum nodes created the *illusion* of dedup while actually fragmenting knowledge — Roger II as `sicily:roger_ii` and `medieval_europe:roger_ii` are two separate `knowledge_items` today. Entity-first dedup is strictly superior.

### 2. Knowledge State Tracking
**Audit said**: Essential — 60-80 nodes per domain is cognitively manageable.

**Re-evaluation**: Valid concern. Raw entities (570+ and growing) are too granular for "how am I doing?" feedback. But the tracking granularity and the *display* granularity don't have to be the same. Track per-entity, display per-curriculum (or per-topic-cluster, or per-era). The curriculum becomes a *view*, not the data model.

### 3. Voice Elicitation
**Audit said**: Essential — curriculum nodes provide the curated topic list.

**Re-evaluation**: Partially true. "Test me on Byzantine topics" needs a curated list. But voice elicitation could also work with entities: "What do you know about Roger II?" doesn't need a curriculum. The curated list is valuable for *structured* elicitation (sweep an era); entity-based elicitation works for *targeted* recall.

### 4. Deterministic Questions
**Audit said**: Essential — `key_facts` on nodes enable instant generation without LLM.

**Re-evaluation**: This is a real value-add. `key_facts` are hand-curated structured data that generate questions deterministically. But `key_facts` could live on entities instead of (or in addition to) curriculum nodes. The generation step that creates `key_facts` currently uses curriculum context — it would need entity context instead.

### 5. Statistics Dashboard
**Audit said**: Essential — knowledge bars per curriculum.

**Re-evaluation**: Display concern, not data model concern. Can compute "Sicily coverage" by counting entities that are tagged with Sicily-related QIDs or that belong to a Sicily curriculum overlay.

### 6. Gap Detection
**Not in audit, but critical**: "What don't I know about Sicily?" requires a bounded set to measure against.

**Re-evaluation**: This is the strongest argument for keeping curricula. Entity graphs don't have natural boundaries — there's always another person, another event. A curriculum says "these 67 things constitute a solid understanding of Sicily." Without that boundary, you can't measure coverage or detect gaps.

**Verdict**: Curricula remain valuable for gap analysis, structured review (structural cards), and progress visualization. They are NOT needed for knowledge tracking, deduplication, voice capture processing, or question generation.

## Dependency Audit: What Breaks

### HARD blockers (must change for entity-first)

1. **`knowledge_items` table** — PK is `{domain}:{node_id}`, `curriculum_domain` and `curriculum_node_id` are `NOT NULL`. Every knowledge tracking flow goes through this table. **This is the single biggest migration.**

2. **`knowledge_states` table** — PK is `(domain_id, node_id)`, foreign key to `curriculum_nodes`. Knowledge levels (unknown/mentioned/engaged/anchored) are tracked per curriculum node.

3. **Voice capture node assessment** (`review_engine.py:4738-4831`) — Creates `knowledge_items` keyed by `{domain}:{node_id}`. Without curriculum nodes to map to, this entire section needs rethinking.

4. **Question generation** (`review_engine.py:1439-1617`) — Loads curriculum via `load_curriculum(domain_id)`, pulls `key_facts`, sibling nodes, prerequisites. Rich context generation depends on curriculum structure.

5. **Review stream** (`curriculum_db.py:1073-1400`) — Queries `knowledge_items` joined to `curriculum_nodes` and `knowledge_states`. Domain round-robin interleaving assumes curriculum domains.

### SOFT dependencies (would need updates but aren't blockers)

6. **Microlearning cards** — `source_node_id` and `source_domain` are nullable. Already work without curriculum.

7. **Structural cards** — Fundamentally curriculum-dependent, but could remain as a curriculum-only feature.

8. **Statistics** — Grouped by domain. Could group by entity cluster or topic instead.

9. **Entity curriculum links** — Bridge table. Becomes one of many relationship types rather than the primary one.

### NO dependency

10. **FSRS scheduling** — Works on any item with `fsrs_card_json`.
11. **Interaction logging** — Curriculum-independent.
12. **Wikidata resolution** — Already entity-first.

## Migration Strategy

### Phase 0: Fix Immediate Blockers (NOW)
- [x] `STRUCTURAL_ONLY = False` — restore mixed review stream
- [x] Gemini API key in systemd `EnvironmentFile`
- [ ] Reprocess 178 pending ML cards

### Phase 1: Entity-Keyed Knowledge (NEW TABLE, parallel to existing)

**Goal**: Voice captures create entity-keyed knowledge without routing through curriculum.

**New table**: `knowledge_entities`
```sql
CREATE TABLE IF NOT EXISTS knowledge_entities (
    id TEXT PRIMARY KEY,              -- entity_id or wikidata QID
    entity_id TEXT NOT NULL REFERENCES shared_entities(entity_id),
    wikidata_qid TEXT,                -- canonical QID if resolved
    name TEXT NOT NULL,
    entity_type TEXT,                 -- person, event, place, work, concept
    -- Knowledge state (same levels as knowledge_states)
    knowledge TEXT NOT NULL DEFAULT 'unknown',
    confidence REAL DEFAULT 0.0,
    -- FSRS scheduling (same as knowledge_items)
    stability_days REAL NOT NULL DEFAULT 1.0,
    due_at INTEGER NOT NULL DEFAULT 0,
    last_reviewed_at INTEGER,
    last_score TEXT,
    review_count INTEGER NOT NULL DEFAULT 0,
    fsrs_card_json TEXT,
    -- Content
    facts TEXT NOT NULL DEFAULT '[]',     -- JSON: accumulated facts from all sources
    sources TEXT NOT NULL DEFAULT '[]',   -- JSON: voice captures, books, etc.
    cached_question TEXT,                 -- JSON: pre-generated question
    question_history TEXT NOT NULL DEFAULT '[]',
    -- Metadata
    date_start INTEGER,
    date_end INTEGER,
    created_at INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_ke_qid ON knowledge_entities(wikidata_qid);
CREATE INDEX IF NOT EXISTS idx_ke_due ON knowledge_entities(due_at);
CREATE INDEX IF NOT EXISTS idx_ke_type ON knowledge_entities(entity_type);
```

**Voice capture flow change**: 
1. Extract entities from transcript (existing)
2. Resolve to Wikidata QIDs (existing, but now synchronous for primary entities)
3. Create/update `knowledge_entities` rows directly (NEW — no curriculum routing)
4. Optionally link to curriculum if a mapping exists (via `entity_curriculum_links`)
5. Generate questions from entity context + captured facts (NEW)

**Key decision**: `knowledge_entities` runs parallel to `knowledge_items` during migration. Both feed the review stream. Over time, new knowledge goes to entities; old curriculum-keyed items continue working.

### Phase 2: Entity-Based Question Generation

**Goal**: Generate review questions from entity context without curriculum.

**Context sources** (replacing curriculum-based context):
- Entity's own accumulated facts (from all voice captures and books)
- Related entities via Wikidata properties (P22=father, P26=spouse, P27=country, P131=located in)
- Temporal neighbors (entities with overlapping date ranges, from `shared_entities.date_start/date_end`)
- Co-occurrence in voice captures ("you mentioned X and Y together")
- Entity type-specific templates (person: reign/achievements/succession; event: causes/consequences/participants; place: significance/events)

**Question quality concern**: Curriculum-based questions benefit from curated `key_facts` and deliberate pedagogical framing. Entity-based questions might be shallower. Mitigation: use Wikidata claims + captured facts as structured seed data, similar to how `key_facts` work today.

### Phase 3: Review Stream Integration

**Goal**: Unified stream that mixes entity-keyed and curriculum-keyed items.

- `generate_review_stream()` queries both `knowledge_items` (curriculum) and `knowledge_entities` (entity-first)
- Same FSRS scoring, same due-date logic
- Entity items get their own origin badge (Wikidata logo? Entity type icon?)
- Curriculum items continue working exactly as today

### Phase 4: Curriculum as Overlay

**Goal**: Curricula become a lens for viewing and organizing entity knowledge.

- "Show me my Sicily coverage" queries entities that are linked to Sicily curriculum nodes
- Gap analysis: "which Sicily curriculum nodes don't have a corresponding entity I know?"
- Structural cards remain curriculum-backed (this is their natural home)
- Voice elicitation can use either curriculum topic list OR entity-based prompting
- Knowledge bars on dashboard computed from entity knowledge aggregated through curriculum lens

### Phase 5: Organic Growth (Future)

- Cluster detection: "you've captured 5+ entities about medieval Scandinavia — generate a curriculum?"
- Entity profiles (from `entity-profiles-design.md`) become the natural intermediate structure
- Curricula generated on demand from entity clusters rather than pre-generated for every possible domain

## What This Means for Each Active Subsystem

### Voice Capture (biggest beneficiary)
Today: Must find curriculum nodes or fall through to weak ML fallback.
After: Extracts entities, resolves QIDs, creates `knowledge_entities` directly. Every voice capture produces reviewable knowledge, regardless of curriculum coverage.

### Review Stream
Today: `knowledge_items` (curriculum-keyed) + `microlearning_cards` + `structural_cards`.
After: `knowledge_entities` (entity-keyed) + `knowledge_items` (legacy curriculum) + `microlearning_cards` + `structural_cards`. Unified scoring and interleaving.

### Structural Cards (aspect, sequence)
Today: Curriculum-dependent.
After: Still curriculum-dependent. Structural cards are inherently about pedagogical structure — they test "how does Greek society evolve across 3 centuries?" which requires a deliberate scope. This is where curricula remain indispensable.

### Voice Elicitation
Today: Curriculum node list with knowledge states.
After: Dual mode — "test me on Byzantine" uses curriculum nodes; "what do you know about Rollo?" uses entity profile. Both valid.

### Books/Chapters
Today: Map chapters to curriculum nodes via LLM.
After: Can still do this AND extract entities directly from chapter text. Entity extraction doesn't need curriculum. Chapter→curriculum mapping becomes an enrichment step, not a prerequisite.

### Statistics Dashboard
Today: Knowledge bars per curriculum domain.
After: Multiple views — per curriculum (overlay), per entity type, per era, per source. More flexible.

## Open Questions

1. **When to resolve QIDs synchronously vs. background?** Currently background-only. For entity-first, we need QIDs before creating `knowledge_entities`. Could resolve primary entities (mentioned >2 times) synchronously and long-tail ones in background.

2. **Entity granularity**: Is "Battle of Poltava" an entity? Is "Norman Conquest of Sicily" an entity? Or are those events that connect entities? Wikidata has items for all of these, but tracking knowledge at the battle level might be too granular. **Proposal**: Track persons, dynasties, and places as primary entities. Events and concepts as secondary (linked to primary entities, not independently scheduled for review).

3. **Fact accumulation vs. fact curation**: Voice captures produce raw facts ("Rollo was the first ruler of Normandy"). These accumulate on `knowledge_entities.facts`. But raw facts aren't curated like `key_facts` — they may be wrong, redundant, or trivially obvious. **Need**: some quality gate, possibly LLM-based, for facts that enter the review pipeline.

4. **Migration of existing `knowledge_items`**: 265 items, 95 with cached questions. Can we auto-migrate by looking up the `entity_curriculum_links` for each node and creating corresponding `knowledge_entities`? Or run them in parallel indefinitely?

5. **Question quality without curriculum context**: Curriculum provides rich context for question generation (sibling nodes, prerequisites, cross-curriculum references). Entity-based context (Wikidata relations, temporal neighbors) is different in character. Need to validate that entity-based questions are as good. **Proposal**: Generate both types for a sample and compare.

## Connections to Design Principles

| Principle | How entity-first serves it |
|-----------|---------------------------|
| "Hooks, not facts" | Entities ARE the hooks — people, events, places are what you anchor new knowledge to |
| "I'll manage your memory" | Track everything you discuss, regardless of whether a curriculum exists |
| "Comprehension before memory" | Entity profiles have layers (identity → context → connections → significance) |
| "Atomic claims" | Facts on entities are claims. QID-based dedup is superior to node-based |
| "Curriculum as bridge" | Curricula become exactly this — a bridge/overlay, not the foundation |
| "Temporal hooks" | Wikidata date ranges enable temporal linking without curriculum cross-references |
| "Facts first, then concepts" | Entity facts (dates, roles, events) are the scaffolding; curriculum provides the conceptual layer on top |
| "Books encode, system maintains" | Books feed entity facts; system maintains via entity graph + FSRS |

## Risk Assessment

**Low risk**: FSRS, interaction logging, review UX, Wikidata resolution — all curriculum-independent already.

**Medium risk**: Question generation quality. Curriculum context is rich and curated; entity context is broader but potentially shallower. Mitigation: run both in parallel, compare quality.

**High risk**: `knowledge_items` migration. This table is the hub of the entire system. Running two parallel systems adds complexity. Mitigation: Phase 1 runs both tables; migration is gradual, not big-bang.

**Biggest risk**: Losing the "bounded set" property of curricula. Entities are unbounded — there's always another person, another event. Without curricula saying "these 67 things are what matters for Sicily," there's no way to measure coverage or feel "done." **This is why curricula remain as overlays, not eliminated.**

## Summary

Curricula are valuable for what they are: curated, bounded, pedagogical perspectives on a domain. They should NOT be the prerequisite for tracking knowledge. The entity layer (Wikidata QIDs, structured metadata, relationship graph) is the natural foundation. Curricula become one way to *view* and *organize* entity knowledge — important for gap analysis, structured review, and progress tracking, but not required for knowledge ingestion.

The phased migration lets us build entity-first voice capture immediately while preserving everything that works about the current system.
