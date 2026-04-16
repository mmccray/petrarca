"""Tests for Phase 2 entity-graph context helpers in review_engine.py.

Covers SQL query shape + formatting logic. Does NOT hit Wikidata or call
LLMs — `_fetch_wikidata_props` is not exercised here (test lives in a
live-API suite if we want it).

Run: cd scripts && python3 -m pytest tests/test_entity_graph_context.py -v
"""

import json
import sqlite3
import sys
import time
from pathlib import Path

import pytest

SCRIPT_DIR = Path(__file__).parent.parent
sys.path.insert(0, str(SCRIPT_DIR))


# ---------- Schema fixture ----------
# Minimal subset of petrarca.db schema needed to exercise the helpers.

SCHEMA = """
CREATE TABLE IF NOT EXISTS shared_entities (
    entity_id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    dates TEXT,
    description TEXT,
    entity_type TEXT,
    date_start INTEGER,
    date_end INTEGER,
    wikidata_qid TEXT
);

CREATE TABLE IF NOT EXISTS knowledge_entities (
    id TEXT PRIMARY KEY,
    entity_id TEXT,
    entity_name TEXT NOT NULL,
    entity_type TEXT,
    wikidata_qid TEXT,
    key_facts TEXT NOT NULL DEFAULT '[]',
    sources TEXT NOT NULL DEFAULT '[]',
    stability_days REAL NOT NULL DEFAULT 1.0,
    due_at INTEGER NOT NULL DEFAULT 0,
    cached_question TEXT,
    question_history TEXT NOT NULL DEFAULT '[]',
    fsrs_card_json TEXT,
    wikidata_props_json TEXT,
    created_at INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS knowledge_items (
    id TEXT PRIMARY KEY,
    curriculum_node_id TEXT NOT NULL,
    curriculum_domain TEXT NOT NULL,
    stability_days REAL NOT NULL DEFAULT 1.0,
    due_at INTEGER NOT NULL DEFAULT 0,
    review_count INTEGER NOT NULL DEFAULT 0,
    sources TEXT NOT NULL DEFAULT '[]',
    question_history TEXT NOT NULL DEFAULT '[]',
    created_at INTEGER NOT NULL,
    UNIQUE(curriculum_domain, curriculum_node_id)
);

CREATE TABLE IF NOT EXISTS entity_curriculum_links (
    entity_id TEXT NOT NULL,
    domain_id TEXT NOT NULL,
    node_id TEXT NOT NULL,
    PRIMARY KEY (entity_id, domain_id, node_id)
);

CREATE TABLE IF NOT EXISTS voice_transcripts (
    id TEXT PRIMARY KEY,
    source TEXT NOT NULL,
    transcript TEXT NOT NULL,
    llm_result TEXT,
    created_at INTEGER NOT NULL
);
"""


@pytest.fixture
def db():
    conn = sqlite3.connect(':memory:')
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)

    # Seed: Battle of Poltava (target entity) + Karl XII + a few others
    entities = [
        # entity_id, name, description, entity_type, date_start, date_end, qid
        ('poltava', 'Battle of Poltava',
         'Decisive battle of the Great Northern War', 'battle', 1709, 1709, 'Q152486'),
        ('karl_xii', 'Karl XII of Sweden',
         'King of Sweden from 1697 to 1718', 'person', 1682, 1718, 'Q52934'),
        ('narva', 'Battle of Narva',
         'Swedish victory early in Great Northern War', 'battle', 1700, 1700, 'Q155726'),
        ('peter_great', 'Peter the Great',
         'Tsar of Russia who defeated Karl XII', 'person', 1672, 1725, 'Q8479'),
        ('viking_paris', 'Viking siege of Paris',
         'Viking siege in the Carolingian era', 'event', 885, 886, 'Q741443'),
        # Unanchored (no knowledge_entities and no curriculum link) — must
        # be EXCLUDED from scoped neighbors even though dates overlap.
        ('louis_xiv', 'Louis XIV',
         'Sun King of France', 'person', 1638, 1715, 'Q7742'),
    ]
    conn.executemany(
        'INSERT INTO shared_entities '
        '(entity_id, name, description, entity_type, date_start, date_end, wikidata_qid) '
        'VALUES (?,?,?,?,?,?,?)',
        entities,
    )

    # knowledge_entities: user has captured Karl XII, Battle of Narva, Viking Paris, Poltava itself
    now = int(time.time())
    ke_rows = [
        ('ent:poltava', 'poltava', 'Battle of Poltava', 'battle', 'Q152486'),
        ('ent:karl_xii', 'karl_xii', 'Karl XII of Sweden', 'person', 'Q52934'),
        ('ent:narva', 'narva', 'Battle of Narva', 'battle', 'Q155726'),
        ('ent:viking_paris', 'viking_paris', 'Viking siege of Paris', 'event', 'Q741443'),
        # Peter the Great NOT in knowledge_entities; linked only via curriculum
    ]
    for ke_id, eid, ename, etype, qid in ke_rows:
        conn.execute(
            'INSERT INTO knowledge_entities '
            '(id, entity_id, entity_name, entity_type, wikidata_qid, created_at) '
            'VALUES (?,?,?,?,?,?)',
            (ke_id, eid, ename, etype, qid, now),
        )

    # Peter the Great is linked via a curriculum node → should surface as a
    # neighbor even without its own knowledge_entities row.
    conn.execute(
        'INSERT INTO entity_curriculum_links (entity_id, domain_id, node_id) '
        'VALUES (?, ?, ?)',
        ('peter_great', 'russia', 'peter_great_node'),
    )
    conn.execute(
        'INSERT INTO knowledge_items '
        '(id, curriculum_node_id, curriculum_domain, created_at) '
        'VALUES (?, ?, ?, ?)',
        ('ki:russia:peter_great', 'peter_great_node', 'russia', now),
    )

    # Voice transcripts: two that mention Battle of Poltava + Karl XII + others
    vt_rows = [
        ('vt_1',
         json.dumps({
             'entities_mentioned': ['Battle of Poltava', 'Karl XII of Sweden',
                                     'Peter the Great', 'Great Northern War']
         })),
        ('vt_2',
         json.dumps({
             'entities_mentioned': ['Battle of Poltava', 'Karl XII of Sweden',
                                     'Battle of Narva']
         })),
        # Third transcript does NOT mention Poltava → should not contribute
        ('vt_3',
         json.dumps({
             'entities_mentioned': ['Louis XIV', 'Versailles']
         })),
    ]
    for vt_id, llm_result in vt_rows:
        conn.execute(
            'INSERT INTO voice_transcripts '
            '(id, source, transcript, llm_result, created_at) '
            'VALUES (?, ?, ?, ?, ?)',
            (vt_id, 'review_memo', 'transcript text', llm_result, now),
        )

    conn.commit()
    return conn


# ---------- _get_scoped_temporal_neighbors ----------


def test_scoped_temporal_neighbors_surfaces_user_graph(db):
    from review_engine import _get_scoped_temporal_neighbors

    # Battle of Poltava: 1709, ±50y window = 1659–1759
    neighbors = _get_scoped_temporal_neighbors(
        entity_id='poltava', qid='Q152486',
        date_start=1709, date_end=1709,
        conn=db,
    )
    names = {n['name'] for n in neighbors}

    # Karl XII has knowledge_entities row + overlapping dates → INCLUDED
    assert 'Karl XII of Sweden' in names
    # Battle of Narva has knowledge_entities row + overlaps → INCLUDED
    assert 'Battle of Narva' in names
    # Peter the Great linked via curriculum → INCLUDED
    assert 'Peter the Great' in names
    # Louis XIV overlaps dates but has NO knowledge_entities and NO link → EXCLUDED
    assert 'Louis XIV' not in names
    # Viking Paris is 885-886, way out of window → EXCLUDED
    assert 'Viking siege of Paris' not in names
    # Self excluded
    assert 'Battle of Poltava' not in names


def test_scoped_temporal_neighbors_no_dates_returns_empty(db):
    from review_engine import _get_scoped_temporal_neighbors
    assert _get_scoped_temporal_neighbors(
        entity_id='x', qid=None,
        date_start=None, date_end=None,
        conn=db,
    ) == []


def test_scoped_temporal_neighbors_source_label(db):
    from review_engine import _get_scoped_temporal_neighbors
    neighbors = _get_scoped_temporal_neighbors(
        entity_id='poltava', qid='Q152486',
        date_start=1709, date_end=1709, conn=db,
    )
    by_name = {n['name']: n for n in neighbors}
    # Karl XII is anchored in knowledge_entities
    assert by_name['Karl XII of Sweden']['source'] == 'entity'
    # Peter the Great is only via entity_curriculum_links → 'curriculum'
    assert by_name['Peter the Great']['source'] == 'curriculum'


# ---------- _get_voice_cooccurring_entities ----------


def test_voice_cooccurrence_ranks_by_count(db):
    from review_engine import _get_voice_cooccurring_entities
    cooccur = _get_voice_cooccurring_entities('Battle of Poltava', db)
    names = [c['name'] for c in cooccur]
    # Karl XII appears in both vt_1 and vt_2 → rank 1 with count 2
    assert names[0] == 'Karl XII of Sweden'
    assert cooccur[0]['count'] == 2
    # Louis XIV in vt_3 which does not mention Poltava → not included
    assert 'Louis XIV' not in names
    # Self excluded
    assert 'Battle of Poltava' not in names


def test_voice_cooccurrence_empty_for_unmentioned(db):
    from review_engine import _get_voice_cooccurring_entities
    assert _get_voice_cooccurring_entities('Nonexistent Entity', db) == []


# ---------- _format_entity_graph_context ----------


def test_format_entity_graph_context_full():
    from review_engine import _format_entity_graph_context

    props = {
        'P569': [{'time': '+1682-06-17T00:00:00Z'}],
        'P570': [{'time': '+1718-11-30T00:00:00Z'}],
        'P22': [{'qid': 'Q312633', 'label': 'Charles XI of Sweden'}],
        'P1366': [{'qid': 'Q312678', 'label': 'Ulrika Eleonora'}],
    }
    neighbors = [
        {'name': 'Peter the Great', 'description': 'Tsar of Russia',
         'date_start': 1672, 'date_end': 1725, 'entity_type': 'person'},
        {'name': 'Louis XIV', 'description': 'Sun King of France',
         'date_start': 1638, 'date_end': 1715, 'entity_type': 'person'},
    ]
    cooccur = [
        {'name': 'Peter the Great', 'count': 3},
        {'name': 'Battle of Narva', 'count': 1},
    ]
    out = _format_entity_graph_context('Karl XII of Sweden', props, neighbors, cooccur)

    assert 'Wikidata properties for Karl XII of Sweden' in out
    assert 'born: 1682' in out
    assert 'died: 1718' in out
    assert 'father: Charles XI of Sweden' in out
    assert 'succeeded by: Ulrika Eleonora' in out
    assert "Other entities you've captured from the same period" in out
    assert 'Peter the Great' in out
    assert 'Entities you\'ve discussed alongside Karl XII of Sweden' in out
    assert 'mentioned together in 3 captures' in out


def test_format_entity_graph_context_empty_blocks_omitted():
    from review_engine import _format_entity_graph_context
    # No props, no neighbors, no co-occur → empty string
    assert _format_entity_graph_context('Some Entity', {}, [], []) == ''


def test_format_entity_graph_context_partial():
    from review_engine import _format_entity_graph_context
    # Only co-occurrence
    out = _format_entity_graph_context(
        'X', {}, [], [{'name': 'Y', 'count': 1}],
    )
    assert 'Wikidata properties' not in out
    assert 'Other entities' not in out
    assert 'mentioned together in 1 capture' in out  # singular
