-- v1 migration: pipelines + pipeline_stages + deals + tasks + forms +
-- form_submissions + idempotency_keys.
-- Idempotent — safe to re-run on a fresh v0 install or an existing one.

PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS pipelines (
  id          INTEGER PRIMARY KEY AUTOINCREMENT,
  name        TEXT    NOT NULL,
  type        TEXT    NOT NULL,                          -- sales|client|award|sponsor|collab|other
  description TEXT,
  archived    INTEGER NOT NULL DEFAULT 0,
  created_at  INTEGER NOT NULL,
  updated_at  INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_pipelines_archived ON pipelines(archived);
CREATE INDEX IF NOT EXISTS idx_pipelines_type ON pipelines(type);

CREATE TABLE IF NOT EXISTS pipeline_stages (
  id          INTEGER PRIMARY KEY AUTOINCREMENT,
  pipeline_id INTEGER NOT NULL REFERENCES pipelines(id) ON DELETE CASCADE,
  name        TEXT    NOT NULL,
  position    INTEGER NOT NULL,
  is_won      INTEGER NOT NULL DEFAULT 0,
  is_lost     INTEGER NOT NULL DEFAULT 0,
  created_at  INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_stages_pipeline ON pipeline_stages(pipeline_id, position);

CREATE TABLE IF NOT EXISTS deals (
  id             INTEGER PRIMARY KEY AUTOINCREMENT,
  contact_id     INTEGER REFERENCES contacts(id) ON DELETE SET NULL,
  company_id     INTEGER REFERENCES companies(id) ON DELETE SET NULL,
  pipeline_id    INTEGER NOT NULL REFERENCES pipelines(id) ON DELETE RESTRICT,
  stage_id       INTEGER NOT NULL REFERENCES pipeline_stages(id) ON DELETE RESTRICT,
  title          TEXT    NOT NULL,
  value_cents    INTEGER,                                 -- minor units (cents); null = unknown
  currency       TEXT,                                    -- iso 4217 lowercase, optional
  probability    INTEGER,                                 -- 0-100
  expected_close INTEGER,                                 -- unix seconds
  status         TEXT NOT NULL CHECK (status IN ('open','won','lost','nurture')) DEFAULT 'open',
  next_step      TEXT,
  notes          TEXT,
  assigned_to    INTEGER REFERENCES users(id) ON DELETE SET NULL,
  created_at     INTEGER NOT NULL,
  updated_at     INTEGER NOT NULL,
  closed_at      INTEGER
);
CREATE INDEX IF NOT EXISTS idx_deals_pipeline_stage ON deals(pipeline_id, stage_id);
CREATE INDEX IF NOT EXISTS idx_deals_contact ON deals(contact_id);
CREATE INDEX IF NOT EXISTS idx_deals_company ON deals(company_id);
CREATE INDEX IF NOT EXISTS idx_deals_status ON deals(status);
CREATE INDEX IF NOT EXISTS idx_deals_assigned ON deals(assigned_to);
CREATE INDEX IF NOT EXISTS idx_deals_close ON deals(expected_close);

CREATE TABLE IF NOT EXISTS tasks (
  id           INTEGER PRIMARY KEY AUTOINCREMENT,
  contact_id   INTEGER REFERENCES contacts(id) ON DELETE CASCADE,
  company_id   INTEGER REFERENCES companies(id) ON DELETE CASCADE,
  deal_id      INTEGER REFERENCES deals(id) ON DELETE CASCADE,
  assigned_to  INTEGER REFERENCES users(id) ON DELETE SET NULL,
  title        TEXT NOT NULL,
  description  TEXT,
  due_date     INTEGER,
  priority     TEXT NOT NULL CHECK (priority IN ('low','normal','high','urgent')) DEFAULT 'normal',
  status       TEXT NOT NULL CHECK (status IN ('open','in_progress','done','cancelled')) DEFAULT 'open',
  created_by   INTEGER REFERENCES users(id) ON DELETE SET NULL,
  created_at   INTEGER NOT NULL,
  updated_at   INTEGER NOT NULL,
  completed_at INTEGER
);
CREATE INDEX IF NOT EXISTS idx_tasks_assigned ON tasks(assigned_to, status, due_date);
CREATE INDEX IF NOT EXISTS idx_tasks_contact ON tasks(contact_id);
CREATE INDEX IF NOT EXISTS idx_tasks_company ON tasks(company_id);
CREATE INDEX IF NOT EXISTS idx_tasks_deal ON tasks(deal_id);
CREATE INDEX IF NOT EXISTS idx_tasks_due ON tasks(due_date);
CREATE INDEX IF NOT EXISTS idx_tasks_status ON tasks(status);

CREATE TABLE IF NOT EXISTS forms (
  id           INTEGER PRIMARY KEY AUTOINCREMENT,
  slug         TEXT    NOT NULL UNIQUE,
  name         TEXT    NOT NULL,
  description  TEXT,
  schema_json  TEXT    NOT NULL,                       -- field definitions
  routing_json TEXT,                                    -- routing rules (interests->tags, etc.)
  redirect_url TEXT,
  active       INTEGER NOT NULL DEFAULT 1,
  created_by   INTEGER REFERENCES users(id) ON DELETE SET NULL,
  created_at   INTEGER NOT NULL,
  updated_at   INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_forms_active ON forms(active);

CREATE TABLE IF NOT EXISTS form_submissions (
  id           INTEGER PRIMARY KEY AUTOINCREMENT,
  form_id      INTEGER NOT NULL REFERENCES forms(id) ON DELETE CASCADE,
  payload_json TEXT    NOT NULL,
  contact_id   INTEGER REFERENCES contacts(id) ON DELETE SET NULL,
  ip           TEXT,
  user_agent   TEXT,
  source       TEXT,
  created_at   INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_form_subs_form ON form_submissions(form_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_form_subs_contact ON form_submissions(contact_id);

CREATE TABLE IF NOT EXISTS idempotency_keys (
  key         TEXT    NOT NULL,
  principal   TEXT    NOT NULL,
  action      TEXT    NOT NULL,
  result_json TEXT    NOT NULL,
  created_at  INTEGER NOT NULL,
  expires_at  INTEGER NOT NULL,
  PRIMARY KEY (key, principal, action)
);
CREATE INDEX IF NOT EXISTS idx_idem_expires ON idempotency_keys(expires_at);

INSERT INTO schema_versions (version, applied_at, description)
SELECT 2, strftime('%s','now'),
       'v1: pipelines, pipeline_stages, deals, tasks, forms, form_submissions, idempotency_keys'
WHERE NOT EXISTS (SELECT 1 FROM schema_versions WHERE version = 2);
