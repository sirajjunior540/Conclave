CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    slack_user_id TEXT UNIQUE NOT NULL,
    display_name TEXT,
    token TEXT UNIQUE NOT NULL,
    created_at TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS sessions (
    id TEXT PRIMARY KEY,
    goal TEXT NOT NULL,
    seed_context TEXT,
    status TEXT NOT NULL DEFAULT 'active',
    created_by INTEGER NOT NULL REFERENCES users(id),
    created_at TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS members (
    session_id TEXT NOT NULL REFERENCES sessions(id),
    user_id INTEGER NOT NULL REFERENCES users(id),
    role TEXT NOT NULL,
    PRIMARY KEY (session_id, user_id, role)
);

CREATE TABLE IF NOT EXISTS nodes (
    id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL REFERENCES sessions(id),
    prompt TEXT NOT NULL,
    assignee_role TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'active',
    decision TEXT,
    public_rationale TEXT,
    decided_by INTEGER REFERENCES users(id),
    decided_at TEXT,
    depends_on TEXT NOT NULL DEFAULT '[]',
    created_at TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS public_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT NOT NULL REFERENCES sessions(id),
    type TEXT NOT NULL,
    payload TEXT NOT NULL,
    ts TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS claim_codes (
    code TEXT PRIMARY KEY,
    slack_user_id TEXT NOT NULL,
    display_name TEXT,
    expires_at TEXT NOT NULL,
    consumed_at TEXT
);
