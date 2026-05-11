-- v2 migration: contact_scores + segments + segment_members.
-- Idempotent.

PRAGMA foreign_keys = ON;

-- Per-contact, per-score-type. One row per (contact, type).
CREATE TABLE IF NOT EXISTS contact_scores (
  contact_id    INTEGER NOT NULL REFERENCES contacts(id) ON DELETE CASCADE,
  score_type    TEXT    NOT NULL CHECK (score_type IN
                  ('relationship_strength','intent','fit','risk','opportunity')),
  score         INTEGER NOT NULL,                  -- 0..100
  evidence_json TEXT,                              -- list of {reason, delta} entries
  computed_at   INTEGER NOT NULL,
  PRIMARY KEY (contact_id, score_type)
);
CREATE INDEX IF NOT EXISTS idx_scores_type_value ON contact_scores(score_type, score);
CREATE INDEX IF NOT EXISTS idx_scores_computed ON contact_scores(computed_at);

-- Segments: static (explicit list) or dynamic (rule-evaluated cached members).
CREATE TABLE IF NOT EXISTS segments (
  id                 INTEGER PRIMARY KEY AUTOINCREMENT,
  name               TEXT    NOT NULL,
  slug               TEXT    NOT NULL UNIQUE,
  type               TEXT    NOT NULL CHECK (type IN ('static','dynamic')),
  rules_json         TEXT,                          -- null for static; rules tree for dynamic
  last_evaluated_at  INTEGER,
  member_count       INTEGER NOT NULL DEFAULT 0,
  created_by         INTEGER REFERENCES users(id) ON DELETE SET NULL,
  created_at         INTEGER NOT NULL,
  updated_at         INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_segments_type ON segments(type);

CREATE TABLE IF NOT EXISTS segment_members (
  segment_id  INTEGER NOT NULL REFERENCES segments(id) ON DELETE CASCADE,
  contact_id  INTEGER NOT NULL REFERENCES contacts(id) ON DELETE CASCADE,
  added_at    INTEGER NOT NULL,
  PRIMARY KEY (segment_id, contact_id)
);
CREATE INDEX IF NOT EXISTS idx_segment_members_contact ON segment_members(contact_id);

INSERT INTO schema_versions (version, applied_at, description)
SELECT 4, strftime('%s','now'),
       'v2: contact_scores + segments + segment_members (rule-based, no LLM)'
WHERE NOT EXISTS (SELECT 1 FROM schema_versions WHERE version = 4);
