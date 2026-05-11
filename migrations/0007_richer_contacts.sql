-- v4.1 migration: richer contact model.
-- Adds the human-shape fields the Blueprint asked for that v0 didn't ship:
--   psychology / interests / social presence / communication preferences /
--   lead source / "about" bio / do-not-contact gate.
--
-- SQLite doesn't support ADD COLUMN IF NOT EXISTS — the migration runner
-- only applies each migration once (tracked in schema_versions), so plain
-- ADD COLUMN is safe.

PRAGMA foreign_keys = ON;

ALTER TABLE contacts ADD COLUMN birthday TEXT;             -- ISO YYYY-MM-DD
ALTER TABLE contacts ADD COLUMN pronouns TEXT;
ALTER TABLE contacts ADD COLUMN language TEXT;             -- BCP-47 like 'en' or 'fr-CA'

ALTER TABLE contacts ADD COLUMN linkedin_url TEXT;
ALTER TABLE contacts ADD COLUMN twitter_url  TEXT;
ALTER TABLE contacts ADD COLUMN instagram_url TEXT;
ALTER TABLE contacts ADD COLUMN website_url   TEXT;

ALTER TABLE contacts ADD COLUMN about TEXT;                -- longer free-form bio
ALTER TABLE contacts ADD COLUMN interests_json TEXT;       -- JSON array of strings

ALTER TABLE contacts ADD COLUMN source TEXT;               -- e.g. 'form:contact-us', 'manual', 'import'
ALTER TABLE contacts ADD COLUMN referrer TEXT;             -- who referred them

ALTER TABLE contacts ADD COLUMN best_contact_window TEXT;  -- e.g. 'weekday mornings PT'
ALTER TABLE contacts ADD COLUMN do_not_contact INTEGER NOT NULL DEFAULT 0;

CREATE INDEX IF NOT EXISTS idx_contacts_source ON contacts(source);
CREATE INDEX IF NOT EXISTS idx_contacts_dnc ON contacts(do_not_contact);

INSERT INTO schema_versions (version, applied_at, description)
SELECT 7, strftime('%s','now'),
       'v4.1 richer contacts: birthday, pronouns, language, socials, about, interests, source, referrer, best_contact_window, do_not_contact'
WHERE NOT EXISTS (SELECT 1 FROM schema_versions WHERE version = 7);
