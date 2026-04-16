#!/usr/bin/env python3
"""Detect potential structural cards from voice transcript entity co-occurrences.

Scans entities_mentioned from voice transcripts, resolves to shared_entities
(for dates/domains), and detects:

1. **Temporal sequences**: Same-domain entities with date gaps < 200 years
   → suggest sequence cards
2. **Contemporaries**: Different-domain entities with overlapping lifetimes
   → suggest synchronic cards

Stores suggestions in the suggested_cards table for admin review.
"""
import hashlib
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from db import get_connection, init_db


def _entity_key(name: str) -> str:
    """Normalize entity name for matching."""
    import re
    return re.sub(r'\W+', '_', name.lower()).strip('_')


def _load_transcript_entities(conn) -> list[dict]:
    """Load voice transcripts with their entities_mentioned + resolve to shared_entities."""
    rows = conn.execute("""
        SELECT id, source, transcript, llm_result, domain_id
        FROM voice_transcripts
        WHERE source IN ('voice_capture', 'voice_capture_entity')
        ORDER BY created_at
    """).fetchall()

    # Build shared_entities lookup by name slug and by name
    se_by_slug = {}
    se_by_name_lower = {}
    for r in conn.execute("""
        SELECT entity_id, name, entity_type, date_start, date_end
        FROM shared_entities
        WHERE date_start IS NOT NULL
    """).fetchall():
        se_by_slug[r['entity_id']] = dict(r)
        se_by_name_lower[r['name'].lower()] = dict(r)

    # Build entity→domain mapping
    entity_domains = {}
    for r in conn.execute("""
        SELECT entity_id, domain_id FROM entity_curriculum_links
    """).fetchall():
        entity_domains.setdefault(r['entity_id'], set()).add(r['domain_id'])

    transcripts = []
    for row in rows:
        if not row['llm_result']:
            continue
        try:
            lr = json.loads(row['llm_result'])
        except (json.JSONDecodeError, TypeError):
            continue

        entities_mentioned = lr.get('entities_mentioned', [])
        if len(entities_mentioned) < 2:
            continue

        # Resolve each entity to shared_entities
        resolved = []
        for ename in entities_mentioned:
            ename_str = str(ename).strip()
            slug = _entity_key(ename_str)
            se = se_by_slug.get(slug) or se_by_name_lower.get(ename_str.lower())
            if not se:
                continue
            domains = entity_domains.get(se['entity_id'], set())
            if row['domain_id'] and row['domain_id'] != 'none':
                domains.add(row['domain_id'])
            resolved.append({
                'name': se['name'],
                'entity_id': se['entity_id'],
                'entity_type': se['entity_type'],
                'date_start': se['date_start'],
                'date_end': se['date_end'],
                'domains': domains,
            })

        if len(resolved) >= 2:
            transcripts.append({
                'id': row['id'],
                'domain_id': row['domain_id'],
                'entities': resolved,
            })

    return transcripts


def _detect_sequences(transcripts: list[dict]) -> list[dict]:
    """Detect temporal sequences: same-domain entities, date gaps < 200 years."""
    suggestions = []

    for t in transcripts:
        # Group entities by domain
        by_domain: dict[str, list] = {}
        for e in t['entities']:
            for d in e['domains']:
                by_domain.setdefault(d, []).append(e)

        for domain_id, entities in by_domain.items():
            # Need at least 3 entities to form a meaningful sequence
            if len(entities) < 3:
                continue

            # Filter to persons/events with clear dates, sort by date
            dated = [e for e in entities if e['date_start'] is not None
                     and e['entity_type'] in ('person', 'event', 'period')]
            dated.sort(key=lambda e: e['date_start'])

            if len(dated) < 3:
                continue

            # Check sequential gaps — all consecutive pairs within 200 years
            gaps_ok = all(
                abs(dated[i+1]['date_start'] - dated[i]['date_start']) <= 200
                for i in range(len(dated) - 1)
            )
            if not gaps_ok:
                continue

            # Check total span is meaningful (at least 50 years)
            span = abs(dated[-1]['date_start'] - dated[0]['date_start'])
            if span < 50:
                continue

            entity_info = [
                {'name': e['name'], 'date_start': e['date_start'],
                 'date_end': e['date_end'], 'type': e['entity_type']}
                for e in dated
            ]
            rationale = (
                f"Temporal sequence in {domain_id}: "
                f"{len(dated)} entities spanning {dated[0]['date_start']}–{dated[-1]['date_start']}, "
                f"max gap ≤200y. From transcript {t['id']}."
            )
            suggestions.append({
                'card_type': 'sequence',
                'source_transcript_ids': [t['id']],
                'entities': entity_info,
                'domain_ids': [domain_id],
                'rationale': rationale,
            })

    return suggestions


def _detect_contemporaries(transcripts: list[dict]) -> list[dict]:
    """Detect contemporaries: different-domain entities with overlapping lifetimes."""
    suggestions = []

    for t in transcripts:
        persons = [e for e in t['entities']
                   if e['entity_type'] == 'person'
                   and e['date_start'] is not None
                   and e['date_end'] is not None]

        if len(persons) < 2:
            continue

        # Find pairs from different domains with overlapping lifetimes
        contemporary_groups: list[list] = []
        used = set()

        for i, a in enumerate(persons):
            if i in used:
                continue
            group = [a]
            group_domains = set(a['domains'])

            for j, b in enumerate(persons[i+1:], i+1):
                if j in used:
                    continue
                # Must be from a different domain
                if b['domains'] == a['domains']:
                    continue
                if b['domains'] & group_domains and not (b['domains'] - group_domains):
                    continue

                # Check lifetime overlap
                overlap_start = max(a['date_start'], b['date_start'])
                overlap_end = min(a['date_end'], b['date_end'])
                if overlap_end - overlap_start >= 10:  # at least 10 years overlap
                    group.append(b)
                    group_domains |= b['domains']
                    used.add(j)

            if len(group) >= 2 and len(group_domains) >= 2:
                used.add(i)
                contemporary_groups.append(group)

        for group in contemporary_groups:
            if len(group) < 2:
                continue
            # Find a representative year (midpoint of overlap)
            earliest_start = max(e['date_start'] for e in group)
            latest_end = min(e['date_end'] for e in group)
            if latest_end < earliest_start:
                continue
            anchor_year = (earliest_start + latest_end) // 2

            all_domains = set()
            for e in group:
                all_domains |= e['domains']

            entity_info = [
                {'name': e['name'], 'date_start': e['date_start'],
                 'date_end': e['date_end'], 'type': e['entity_type']}
                for e in group
            ]
            names = ', '.join(e['name'] for e in group)
            rationale = (
                f"Contemporaries ~{anchor_year}: {names}. "
                f"{len(all_domains)} domains, lifetimes overlap ≥10y. "
                f"From transcript {t['id']}."
            )
            suggestions.append({
                'card_type': 'synchronic',
                'source_transcript_ids': [t['id']],
                'entities': entity_info,
                'domain_ids': sorted(all_domains),
                'rationale': rationale,
            })

    return suggestions


def _dedup_suggestions(suggestions: list[dict], conn) -> list[dict]:
    """Remove suggestions that overlap heavily with existing structural cards or suggestions."""
    # Load existing structural card entity sets for dedup
    existing_entity_sets = set()

    for r in conn.execute("SELECT title, card_type FROM structural_cards"):
        existing_entity_sets.add((r['card_type'], r['title'].lower()))

    for r in conn.execute("SELECT entities, card_type FROM suggested_cards WHERE status != 'rejected'"):
        try:
            ents = json.loads(r['entities'])
            names = frozenset(e['name'].lower() for e in ents)
            existing_entity_sets.add((r['card_type'], ','.join(sorted(names))))
        except (json.JSONDecodeError, TypeError):
            pass

    novel = []
    for s in suggestions:
        names = frozenset(e['name'].lower() for e in s['entities'])
        key = (s['card_type'], ','.join(sorted(names)))
        if key not in existing_entity_sets:
            existing_entity_sets.add(key)
            novel.append(s)

    return novel


def main():
    init_db()  # ensure suggested_cards table exists
    conn = get_connection(readonly=True)

    print("Loading transcript entities...")
    transcripts = _load_transcript_entities(conn)
    print(f"  {len(transcripts)} transcripts with 2+ resolved entities")

    print("\nDetecting temporal sequences...")
    sequences = _detect_sequences(transcripts)
    print(f"  {len(sequences)} potential sequence cards")

    print("\nDetecting contemporaries...")
    contemporaries = _detect_contemporaries(transcripts)
    print(f"  {len(contemporaries)} potential synchronic cards")

    all_suggestions = sequences + contemporaries
    print(f"\nDeduplicating against existing cards...")
    novel = _dedup_suggestions(all_suggestions, conn)
    conn.close()
    print(f"  {len(novel)} novel suggestions (after dedup)")

    if not novel:
        print("\nNo new suggestions to save.")
        return

    # Write to DB
    wconn = get_connection()
    for s in novel:
        sig = hashlib.sha256(json.dumps(s['entities'], sort_keys=True).encode()).hexdigest()[:12]
        sid = f"sc_{s['card_type']}_{sig}"
        wconn.execute("""
            INSERT OR IGNORE INTO suggested_cards
            (id, card_type, source_transcript_ids, entities, domain_ids, rationale)
            VALUES (?, ?, ?, ?, ?, ?)
        """, (
            sid, s['card_type'],
            json.dumps(s['source_transcript_ids']),
            json.dumps(s['entities']),
            json.dumps(s['domain_ids']),
            s['rationale'],
        ))
    wconn.commit()
    wconn.close()

    print(f"\n=== Saved {len(novel)} suggestions ===")
    for s in novel:
        names = ', '.join(e['name'] for e in s['entities'][:5])
        print(f"  [{s['card_type']}] {names}")
        print(f"    {s['rationale'][:100]}")


if __name__ == '__main__':
    main()
