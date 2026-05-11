-- v4 migration: plug-in registry + saved views + role-permissions matrix.

PRAGMA foreign_keys = ON;

-- Plug-ins are Python modules dropped into agent_surface/plugins/. The DB
-- tracks which ones are installed and currently enabled. A plug-in's hooks
-- and tools become live after load + enable.
CREATE TABLE IF NOT EXISTS plugins (
  id          INTEGER PRIMARY KEY AUTOINCREMENT,
  name        TEXT    NOT NULL UNIQUE,
  version     TEXT,
  description TEXT,
  enabled     INTEGER NOT NULL DEFAULT 1,
  config_json TEXT,                              -- plug-in-specific config
  installed_at INTEGER NOT NULL,
  updated_at  INTEGER NOT NULL,
  last_error  TEXT                                -- most recent load/run error
);
CREATE INDEX IF NOT EXISTS idx_plugins_enabled ON plugins(enabled);

-- A plug-in can declare hooks. The registry table tracks which hooks each
-- plug-in attaches to (useful for the admin UI + audit). The actual function
-- references live in-process — this is just metadata.
CREATE TABLE IF NOT EXISTS plugin_hooks (
  id         INTEGER PRIMARY KEY AUTOINCREMENT,
  plugin_id  INTEGER NOT NULL REFERENCES plugins(id) ON DELETE CASCADE,
  hook_name  TEXT    NOT NULL,                    -- e.g. on_contact_created
  priority   INTEGER NOT NULL DEFAULT 100,        -- lower runs first
  created_at INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_plugin_hooks_name ON plugin_hooks(hook_name, priority);

-- Saved views: a stored {entity, name, slug, filter+sort+columns} per user.
-- Used by Contacts/Companies/Deals/Tasks list pages to remember "my warm leads
-- this week" without re-typing the search every time.
CREATE TABLE IF NOT EXISTS saved_views (
  id          INTEGER PRIMARY KEY AUTOINCREMENT,
  user_id     INTEGER REFERENCES users(id) ON DELETE CASCADE,
  entity      TEXT    NOT NULL CHECK (entity IN
                ('contact','company','deal','task','interaction')),
  name        TEXT    NOT NULL,
  slug        TEXT,                                -- optional shareable id
  config_json TEXT    NOT NULL,                   -- {filter, sort, columns, ...}
  shared      INTEGER NOT NULL DEFAULT 0,         -- visible to all users
  created_at  INTEGER NOT NULL,
  updated_at  INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_saved_views_user ON saved_views(user_id, entity);
CREATE INDEX IF NOT EXISTS idx_saved_views_shared ON saved_views(shared, entity);

-- Roles + role_permissions: simple opt-in granular RBAC. Core ships with
-- 'admin', 'user', 'readonly' built into users.role. v4 adds named roles
-- with explicit permission sets so a customer can compose their own.
-- Permissions are simple action strings: 'contact.read', 'deal.write', etc.
CREATE TABLE IF NOT EXISTS roles (
  id          INTEGER PRIMARY KEY AUTOINCREMENT,
  name        TEXT    NOT NULL UNIQUE,
  description TEXT,
  built_in    INTEGER NOT NULL DEFAULT 0,
  created_at  INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS role_permissions (
  role_id    INTEGER NOT NULL REFERENCES roles(id) ON DELETE CASCADE,
  permission TEXT    NOT NULL,
  PRIMARY KEY (role_id, permission)
);

CREATE TABLE IF NOT EXISTS user_roles (
  user_id   INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  role_id   INTEGER NOT NULL REFERENCES roles(id) ON DELETE CASCADE,
  granted_at INTEGER NOT NULL,
  granted_by INTEGER REFERENCES users(id) ON DELETE SET NULL,
  PRIMARY KEY (user_id, role_id)
);
CREATE INDEX IF NOT EXISTS idx_user_roles_user ON user_roles(user_id);

-- Seed the built-in roles for granular RBAC. These mirror users.role values
-- but exist as rows so plug-ins / admins can attach extra permissions on top.
INSERT OR IGNORE INTO roles (name, description, built_in, created_at)
VALUES
  ('admin',    'Full access; can manage users, plug-ins, settings', 1, strftime('%s','now')),
  ('user',     'Standard write access to contacts/companies/etc.',  1, strftime('%s','now')),
  ('readonly', 'Read-only access; cannot mutate',                    1, strftime('%s','now'));

INSERT INTO schema_versions (version, applied_at, description)
SELECT 6, strftime('%s','now'),
       'v4: plugins + plugin_hooks + saved_views + roles + role_permissions + user_roles'
WHERE NOT EXISTS (SELECT 1 FROM schema_versions WHERE version = 6);
