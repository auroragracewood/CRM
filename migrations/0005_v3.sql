-- v3 migration: portal_tokens (self-service URLs) + inbound_endpoints (external
-- systems POSTing into the CRM) + inbound_events (the receive log).

PRAGMA foreign_keys = ON;

-- A portal_token grants an EXTERNAL contact read access to their OWN data via
-- a URL like /portal/{token}. No admin login required. Scoped to one contact.
CREATE TABLE IF NOT EXISTS portal_tokens (
  id           INTEGER PRIMARY KEY AUTOINCREMENT,
  token        TEXT    NOT NULL UNIQUE,
  contact_id   INTEGER NOT NULL REFERENCES contacts(id) ON DELETE CASCADE,
  scope        TEXT    NOT NULL CHECK (scope IN
                  ('client','applicant','sponsor','member')) DEFAULT 'client',
  label        TEXT,                                   -- optional human label
  expires_at   INTEGER,                                -- optional expiry
  revoked_at   INTEGER,
  created_by   INTEGER REFERENCES users(id) ON DELETE SET NULL,
  created_at   INTEGER NOT NULL,
  last_used_at INTEGER
);
CREATE INDEX IF NOT EXISTS idx_portal_tokens_contact ON portal_tokens(contact_id);
CREATE INDEX IF NOT EXISTS idx_portal_tokens_revoked ON portal_tokens(revoked_at);

-- An inbound_endpoint listens for external systems to POST events. Each has a
-- unique slug (the URL is /in/{slug}) and an optional shared secret for HMAC
-- verification. The routing_json field describes how to map incoming payloads
-- into contacts + interactions (parse field paths, tag rules, etc.).
CREATE TABLE IF NOT EXISTS inbound_endpoints (
  id               INTEGER PRIMARY KEY AUTOINCREMENT,
  slug             TEXT    NOT NULL UNIQUE,
  name             TEXT    NOT NULL,
  description      TEXT,
  shared_secret    TEXT,                               -- optional HMAC secret
  active           INTEGER NOT NULL DEFAULT 1,
  routing_json     TEXT,                               -- {email_path, name_path, tags, type}
  last_received_at INTEGER,
  created_by       INTEGER REFERENCES users(id) ON DELETE SET NULL,
  created_at       INTEGER NOT NULL,
  updated_at       INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_inbound_endpoints_active ON inbound_endpoints(active);

-- Every POST to /in/{slug} lands here, signed-or-not, before parsing.
-- Successful events get linked to the resulting contact + interaction.
CREATE TABLE IF NOT EXISTS inbound_events (
  id              INTEGER PRIMARY KEY AUTOINCREMENT,
  endpoint_id     INTEGER NOT NULL REFERENCES inbound_endpoints(id) ON DELETE CASCADE,
  raw_payload     TEXT,
  ip              TEXT,
  user_agent      TEXT,
  signature_valid INTEGER NOT NULL DEFAULT 0,
  contact_id      INTEGER REFERENCES contacts(id) ON DELETE SET NULL,
  interaction_id  INTEGER REFERENCES interactions(id) ON DELETE SET NULL,
  status          TEXT NOT NULL CHECK (status IN
                    ('received','parsed','contact_linked','error')) DEFAULT 'received',
  error           TEXT,
  created_at      INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_inbound_events_endpoint
  ON inbound_events(endpoint_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_inbound_events_status ON inbound_events(status);

INSERT INTO schema_versions (version, applied_at, description)
SELECT 5, strftime('%s','now'),
       'v3: portal_tokens + inbound_endpoints + inbound_events'
WHERE NOT EXISTS (SELECT 1 FROM schema_versions WHERE version = 5);
