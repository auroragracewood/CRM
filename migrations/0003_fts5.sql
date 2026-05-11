-- v1 search: FTS5 virtual table over contacts.name, companies.name,
-- interactions.title+body, notes.body. Sync'd via triggers.

CREATE VIRTUAL TABLE IF NOT EXISTS search_index USING fts5(
  kind UNINDEXED,           -- 'contact' | 'company' | 'interaction' | 'note'
  ref  UNINDEXED,           -- the row id in the source table
  title,
  body,
  tokenize = 'porter unicode61'
);

-- Backfill: populate from current data (idempotent — delete-then-insert).
DELETE FROM search_index;

INSERT INTO search_index (kind, ref, title, body)
SELECT 'contact', id, COALESCE(full_name, ''), COALESCE(email, '') || ' ' || COALESCE(title, '') || ' ' || COALESCE(location, '')
  FROM contacts WHERE deleted_at IS NULL;

INSERT INTO search_index (kind, ref, title, body)
SELECT 'company', id, COALESCE(name, ''), COALESCE(domain, '') || ' ' || COALESCE(industry, '') || ' ' || COALESCE(description, '')
  FROM companies WHERE deleted_at IS NULL;

INSERT INTO search_index (kind, ref, title, body)
SELECT 'interaction', id, COALESCE(title, ''), COALESCE(body, '')
  FROM interactions;

INSERT INTO search_index (kind, ref, title, body)
SELECT 'note', id, '', COALESCE(body, '')
  FROM notes WHERE visibility != 'private';

-- Triggers to keep the index in sync.
CREATE TRIGGER IF NOT EXISTS contacts_ai AFTER INSERT ON contacts
WHEN NEW.deleted_at IS NULL
BEGIN
  INSERT INTO search_index (kind, ref, title, body)
  VALUES ('contact', NEW.id, COALESCE(NEW.full_name, ''),
          COALESCE(NEW.email, '') || ' ' || COALESCE(NEW.title, '') || ' ' || COALESCE(NEW.location, ''));
END;
CREATE TRIGGER IF NOT EXISTS contacts_au AFTER UPDATE ON contacts BEGIN
  DELETE FROM search_index WHERE kind='contact' AND ref=NEW.id;
  INSERT INTO search_index (kind, ref, title, body)
  SELECT 'contact', NEW.id, COALESCE(NEW.full_name, ''),
         COALESCE(NEW.email, '') || ' ' || COALESCE(NEW.title, '') || ' ' || COALESCE(NEW.location, '')
   WHERE NEW.deleted_at IS NULL;
END;
CREATE TRIGGER IF NOT EXISTS contacts_ad AFTER DELETE ON contacts BEGIN
  DELETE FROM search_index WHERE kind='contact' AND ref=OLD.id;
END;

CREATE TRIGGER IF NOT EXISTS companies_ai AFTER INSERT ON companies
WHEN NEW.deleted_at IS NULL
BEGIN
  INSERT INTO search_index (kind, ref, title, body)
  VALUES ('company', NEW.id, COALESCE(NEW.name, ''),
          COALESCE(NEW.domain, '') || ' ' || COALESCE(NEW.industry, '') || ' ' || COALESCE(NEW.description, ''));
END;
CREATE TRIGGER IF NOT EXISTS companies_au AFTER UPDATE ON companies BEGIN
  DELETE FROM search_index WHERE kind='company' AND ref=NEW.id;
  INSERT INTO search_index (kind, ref, title, body)
  SELECT 'company', NEW.id, COALESCE(NEW.name, ''),
         COALESCE(NEW.domain, '') || ' ' || COALESCE(NEW.industry, '') || ' ' || COALESCE(NEW.description, '')
   WHERE NEW.deleted_at IS NULL;
END;
CREATE TRIGGER IF NOT EXISTS companies_ad AFTER DELETE ON companies BEGIN
  DELETE FROM search_index WHERE kind='company' AND ref=OLD.id;
END;

CREATE TRIGGER IF NOT EXISTS interactions_ai AFTER INSERT ON interactions BEGIN
  INSERT INTO search_index (kind, ref, title, body)
  VALUES ('interaction', NEW.id, COALESCE(NEW.title, ''), COALESCE(NEW.body, ''));
END;
CREATE TRIGGER IF NOT EXISTS interactions_ad AFTER DELETE ON interactions BEGIN
  DELETE FROM search_index WHERE kind='interaction' AND ref=OLD.id;
END;

-- Notes: only public/team in the index (private notes never indexed).
CREATE TRIGGER IF NOT EXISTS notes_ai AFTER INSERT ON notes
WHEN NEW.visibility != 'private'
BEGIN
  INSERT INTO search_index (kind, ref, title, body)
  VALUES ('note', NEW.id, '', COALESCE(NEW.body, ''));
END;
CREATE TRIGGER IF NOT EXISTS notes_au AFTER UPDATE ON notes BEGIN
  DELETE FROM search_index WHERE kind='note' AND ref=NEW.id;
  INSERT INTO search_index (kind, ref, title, body)
  SELECT 'note', NEW.id, '', COALESCE(NEW.body, '')
   WHERE NEW.visibility != 'private';
END;
CREATE TRIGGER IF NOT EXISTS notes_ad AFTER DELETE ON notes BEGIN
  DELETE FROM search_index WHERE kind='note' AND ref=OLD.id;
END;

INSERT INTO schema_versions (version, applied_at, description)
SELECT 3, strftime('%s','now'), 'v1 search: FTS5 search_index + triggers across contacts/companies/interactions/notes'
WHERE NOT EXISTS (SELECT 1 FROM schema_versions WHERE version = 3);
