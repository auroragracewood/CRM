-- =============================================================================
-- CRM v0 schema. 15 tables. Single-company. Audit-everything. Outbox webhooks.
-- Applied by setup.py / db.apply_schema on first run.
--
-- Note on table count: the Blueprint specs 14 v0 tables; this schema includes
-- `sessions` as the 15th. Cookie sessions need a server-side table to honor
-- "logout invalidates immediately." Sessions are not user data — they're
-- transient auth state.
-- =============================================================================

PRAGMA foreign_keys = ON;

-- Migration tracker. setup.py reads this to decide what to apply.
CREATE TABLE IF NOT EXISTS schema_versions (
  version     INTEGER PRIMARY KEY,
  applied_at  INTEGER NOT NULL,
  description TEXT
);

-- Humans who log into the UI.
CREATE TABLE IF NOT EXISTS users (
  id              INTEGER PRIMARY KEY AUTOINCREMENT,
  email           TEXT    NOT NULL UNIQUE,
  password_hash   TEXT    NOT NULL,
  display_name    TEXT,
  role            TEXT    NOT NULL CHECK (role IN ('admin','user','readonly')) DEFAULT 'user',
  created_at      INTEGER NOT NULL,
  updated_at      INTEGER NOT NULL,
  last_login_at   INTEGER
);
CREATE INDEX IF NOT EXISTS idx_users_email ON users(email);

-- Sessions for cookie-authenticated humans (invalidatable on logout).
CREATE TABLE IF NOT EXISTS sessions (
  id              TEXT    PRIMARY KEY,
  user_id         INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  created_at      INTEGER NOT NULL,
  last_seen_at    INTEGER NOT NULL,
  expires_at      INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_sessions_user ON sessions(user_id);
CREATE INDEX IF NOT EXISTS idx_sessions_expires ON sessions(expires_at);

-- API keys: bearer tokens for agents (REST, MCP, CLI when remote).
CREATE TABLE IF NOT EXISTS api_keys (
  id              INTEGER PRIMARY KEY AUTOINCREMENT,
  user_id         INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  name            TEXT    NOT NULL,
  key_prefix      TEXT    NOT NULL,            -- first ~12 chars of raw key for display
  key_hash        TEXT    NOT NULL UNIQUE,     -- sha256 hash of raw key
  scope           TEXT    NOT NULL CHECK (scope IN ('read','write','admin')) DEFAULT 'write',
  created_at      INTEGER NOT NULL,
  last_used_at    INTEGER,
  revoked_at      INTEGER
);
CREATE INDEX IF NOT EXISTS idx_api_keys_user ON api_keys(user_id);
CREATE INDEX IF NOT EXISTS idx_api_keys_revoked ON api_keys(revoked_at);

-- Audit log: every mutation lands here.
-- Principal is user_id OR api_key_id (at least one must be set on non-system rows).
CREATE TABLE IF NOT EXISTS audit_log (
  id              INTEGER PRIMARY KEY AUTOINCREMENT,
  ts              INTEGER NOT NULL,
  user_id         INTEGER REFERENCES users(id) ON DELETE SET NULL,
  api_key_id      INTEGER REFERENCES api_keys(id) ON DELETE SET NULL,
  surface         TEXT    NOT NULL,            -- ui|rest|cli|mcp|cron|plugin|webhook|system
  action          TEXT    NOT NULL,            -- e.g. contact.created, note.private_revealed
  object_type     TEXT    NOT NULL,            -- contact|company|note|tag|consent|webhook|...
  object_id       INTEGER,
  before_json     TEXT,                        -- null on create
  after_json      TEXT,                        -- null on hard delete
  request_id      TEXT
);
CREATE INDEX IF NOT EXISTS idx_audit_object ON audit_log(object_type, object_id);
CREATE INDEX IF NOT EXISTS idx_audit_ts ON audit_log(ts);
CREATE INDEX IF NOT EXISTS idx_audit_user ON audit_log(user_id);
CREATE INDEX IF NOT EXISTS idx_audit_key ON audit_log(api_key_id);
CREATE INDEX IF NOT EXISTS idx_audit_request ON audit_log(request_id);

-- Companies (organizations).
CREATE TABLE IF NOT EXISTS companies (
  id                 INTEGER PRIMARY KEY AUTOINCREMENT,
  name               TEXT    NOT NULL,
  slug               TEXT    UNIQUE,
  website            TEXT,
  domain             TEXT,                     -- lowercased on write
  industry           TEXT,
  size               TEXT,                     -- '1-10','11-50','51-200','201-500','501-1000','1000+'
  location           TEXT,
  description        TEXT,
  custom_fields_json TEXT,
  created_at         INTEGER NOT NULL,
  updated_at         INTEGER NOT NULL,
  deleted_at         INTEGER                   -- soft-delete; NULL means active
);
CREATE INDEX IF NOT EXISTS idx_companies_name ON companies(name);
CREATE INDEX IF NOT EXISTS idx_companies_domain ON companies(domain);
CREATE INDEX IF NOT EXISTS idx_companies_deleted ON companies(deleted_at);

-- Contacts (people).
CREATE TABLE IF NOT EXISTS contacts (
  id                 INTEGER PRIMARY KEY AUTOINCREMENT,
  full_name          TEXT,
  first_name         TEXT,
  last_name          TEXT,
  email              TEXT,                     -- lowercased+trimmed on write
  phone              TEXT,
  avatar_url         TEXT,
  company_id         INTEGER REFERENCES companies(id) ON DELETE SET NULL,
  title              TEXT,
  location           TEXT,
  timezone           TEXT,
  preferred_channel  TEXT,
  custom_fields_json TEXT,
  created_at         INTEGER NOT NULL,
  updated_at         INTEGER NOT NULL,
  deleted_at         INTEGER
);
CREATE INDEX IF NOT EXISTS idx_contacts_company ON contacts(company_id);
CREATE INDEX IF NOT EXISTS idx_contacts_name ON contacts(full_name);
CREATE INDEX IF NOT EXISTS idx_contacts_deleted ON contacts(deleted_at);

-- Partial unique index: among ACTIVE contacts, email must be unique.
-- Soft-deleted contacts freed the email back up.
CREATE UNIQUE INDEX IF NOT EXISTS uq_contacts_active_email
  ON contacts (email)
  WHERE email IS NOT NULL AND deleted_at IS NULL;

-- Tags.
CREATE TABLE IF NOT EXISTS tags (
  id          INTEGER PRIMARY KEY AUTOINCREMENT,
  name        TEXT    NOT NULL UNIQUE,
  color       TEXT,                              -- e.g. '#a3e3c1'
  scope       TEXT    NOT NULL CHECK (scope IN ('contact','company','any')) DEFAULT 'any',
  created_at  INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS contact_tags (
  contact_id  INTEGER NOT NULL REFERENCES contacts(id) ON DELETE CASCADE,
  tag_id      INTEGER NOT NULL REFERENCES tags(id) ON DELETE CASCADE,
  added_at    INTEGER NOT NULL,
  added_by    INTEGER REFERENCES users(id) ON DELETE SET NULL,
  PRIMARY KEY (contact_id, tag_id)
);
CREATE INDEX IF NOT EXISTS idx_contact_tags_tag ON contact_tags(tag_id);

CREATE TABLE IF NOT EXISTS company_tags (
  company_id  INTEGER NOT NULL REFERENCES companies(id) ON DELETE CASCADE,
  tag_id      INTEGER NOT NULL REFERENCES tags(id) ON DELETE CASCADE,
  added_at    INTEGER NOT NULL,
  added_by    INTEGER REFERENCES users(id) ON DELETE SET NULL,
  PRIMARY KEY (company_id, tag_id)
);
CREATE INDEX IF NOT EXISTS idx_company_tags_tag ON company_tags(tag_id);

-- Interactions: the catch-all timeline event table.
-- type enum: email | call | meeting | form_submission | page_view | note_system | system
CREATE TABLE IF NOT EXISTS interactions (
  id              INTEGER PRIMARY KEY AUTOINCREMENT,
  contact_id      INTEGER REFERENCES contacts(id) ON DELETE CASCADE,
  company_id      INTEGER REFERENCES companies(id) ON DELETE CASCADE,
  type            TEXT    NOT NULL CHECK (type IN
                  ('email','call','meeting','form_submission','page_view','note_system','system')),
  channel         TEXT,
  title           TEXT,
  body            TEXT,
  metadata_json   TEXT,
  source          TEXT,
  occurred_at     INTEGER NOT NULL,
  created_at      INTEGER NOT NULL,
  CHECK (contact_id IS NOT NULL OR company_id IS NOT NULL)
);
CREATE INDEX IF NOT EXISTS idx_interactions_contact ON interactions(contact_id, occurred_at DESC);
CREATE INDEX IF NOT EXISTS idx_interactions_company ON interactions(company_id, occurred_at DESC);
CREATE INDEX IF NOT EXISTS idx_interactions_type ON interactions(type);
CREATE INDEX IF NOT EXISTS idx_interactions_occurred ON interactions(occurred_at);

-- Notes: visibility-scoped human notes (kept separate from interactions for permission control).
-- visibility: public | team | private. Private requires explicit reveal (audited).
CREATE TABLE IF NOT EXISTS notes (
  id          INTEGER PRIMARY KEY AUTOINCREMENT,
  contact_id  INTEGER REFERENCES contacts(id) ON DELETE CASCADE,
  company_id  INTEGER REFERENCES companies(id) ON DELETE CASCADE,
  body        TEXT    NOT NULL,
  visibility  TEXT    NOT NULL CHECK (visibility IN ('public','team','private')) DEFAULT 'team',
  created_by  INTEGER REFERENCES users(id) ON DELETE SET NULL,
  created_at  INTEGER NOT NULL,
  updated_at  INTEGER NOT NULL,
  CHECK (contact_id IS NOT NULL OR company_id IS NOT NULL)
);
CREATE INDEX IF NOT EXISTS idx_notes_contact ON notes(contact_id);
CREATE INDEX IF NOT EXISTS idx_notes_company ON notes(company_id);
CREATE INDEX IF NOT EXISTS idx_notes_visibility ON notes(visibility);

-- Consent: per-contact, per-channel.
CREATE TABLE IF NOT EXISTS consent (
  id            INTEGER PRIMARY KEY AUTOINCREMENT,
  contact_id    INTEGER NOT NULL REFERENCES contacts(id) ON DELETE CASCADE,
  channel       TEXT    NOT NULL,                -- email|sms|phone|marketing|...
  status        TEXT    NOT NULL CHECK (status IN ('granted','withdrawn','unknown')) DEFAULT 'unknown',
  source        TEXT,                            -- form name, manual entry, import
  proof         TEXT,                            -- supporting evidence
  granted_at    INTEGER,
  withdrawn_at  INTEGER,
  created_at    INTEGER NOT NULL,
  updated_at    INTEGER NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS uq_consent_contact_channel ON consent(contact_id, channel);

-- Webhooks: subscription rows.
CREATE TABLE IF NOT EXISTS webhooks (
  id           INTEGER PRIMARY KEY AUTOINCREMENT,
  url          TEXT    NOT NULL,
  events_json  TEXT    NOT NULL,                 -- JSON array of event names
  secret       TEXT    NOT NULL,                 -- HMAC-SHA256 signing secret
  active       INTEGER NOT NULL DEFAULT 1,
  created_at   INTEGER NOT NULL,
  updated_at   INTEGER NOT NULL,
  created_by   INTEGER REFERENCES users(id) ON DELETE SET NULL
);
CREATE INDEX IF NOT EXISTS idx_webhooks_active ON webhooks(active);

-- Webhook events: the outbox. Inserted in same TX as the data mutation;
-- dispatcher delivers after commit, with retry + signing.
CREATE TABLE IF NOT EXISTS webhook_events (
  id              INTEGER PRIMARY KEY AUTOINCREMENT,
  webhook_id      INTEGER NOT NULL REFERENCES webhooks(id) ON DELETE CASCADE,
  event_type      TEXT    NOT NULL,
  payload_json    TEXT    NOT NULL,
  status          TEXT    NOT NULL CHECK (status IN ('pending','retrying','delivered','failed'))
                  DEFAULT 'pending',
  attempts        INTEGER NOT NULL DEFAULT 0,
  response_status INTEGER,
  response_body   TEXT,
  next_attempt_at INTEGER,
  delivery_id     TEXT    NOT NULL UNIQUE,        -- uuid; X-CRM-Delivery-ID header
  created_at      INTEGER NOT NULL,
  delivered_at    INTEGER,
  failed_at       INTEGER
);
CREATE INDEX IF NOT EXISTS idx_webhook_events_status ON webhook_events(status, next_attempt_at);
CREATE INDEX IF NOT EXISTS idx_webhook_events_webhook ON webhook_events(webhook_id);
CREATE INDEX IF NOT EXISTS idx_webhook_events_type ON webhook_events(event_type);

-- Stamp v1 of the schema. setup.py / db.apply_schema only run this script when
-- no rows exist in schema_versions yet.
INSERT INTO schema_versions (version, applied_at, description)
VALUES (1, strftime('%s', 'now'), 'v0 initial — 15 tables: users, sessions, api_keys, audit_log, companies, contacts, tags, contact_tags, company_tags, interactions, notes, consent, webhooks, webhook_events, schema_versions');
