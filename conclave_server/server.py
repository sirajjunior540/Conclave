"""Conclave MCP server — slice 0.

Run:
    pip install fastmcp
    python -m conclave_server.server

The server listens on http://127.0.0.1:8765/mcp by default.

To bootstrap the very first user (so they can authenticate from Claude Code):
    python -m conclave_server.admin issue-code --slack-user U02ABC --name "Siraj"
That prints a one-time claim code. The user runs in Claude Code:
    /grill claim <code>
which exchanges the code for a long-lived token.

Subsequent stakeholders are issued claim codes automatically by create_session
when they're added to a session for the first time. The notification adapter
(notifications.py) "delivers" the code — in v0 that's stderr, in slice 1 it
becomes a Slack DM.
"""
import json
import secrets
from datetime import datetime, timedelta, timezone

from fastmcp import FastMCP

from .db import get_conn, init_db
from .notifications import send_dm

mcp = FastMCP("Conclave")


def _short_id() -> str:
    return secrets.token_urlsafe(6)


def _new_token() -> str:
    return "ct_" + secrets.token_urlsafe(24)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _auth(conn, token: str) -> dict:
    if not token or token.startswith("unbound_"):
        raise PermissionError("invalid auth token")
    row = conn.execute(
        "SELECT id, slack_user_id, display_name FROM users WHERE token = ?", (token,)
    ).fetchone()
    if not row:
        raise PermissionError("invalid auth token")
    return dict(row)


def _roles_in_session(conn, session_id: str, user_id: int) -> list[str]:
    rows = conn.execute(
        "SELECT role FROM members WHERE session_id = ? AND user_id = ?",
        (session_id, user_id),
    ).fetchall()
    return [r["role"] for r in rows]


def _emit(conn, session_id: str, type_: str, payload: dict) -> None:
    conn.execute(
        "INSERT INTO public_events (session_id, type, payload) VALUES (?, ?, ?)",
        (session_id, type_, json.dumps(payload)),
    )


@mcp.tool
def claim_identity(code: str) -> dict:
    """Exchange a one-time claim code for a long-lived auth token.

    Codes are issued by the server admin or automatically when create_session
    adds a stakeholder who has never authenticated before. The token returned
    here should be stored locally by the caller (skill state) and passed as
    `auth_token` on every subsequent tool call.
    """
    with get_conn() as conn:
        row = conn.execute(
            "SELECT slack_user_id, display_name, expires_at, consumed_at "
            "FROM claim_codes WHERE code = ?",
            (code,),
        ).fetchone()
        if not row:
            raise ValueError("unknown claim code")
        if row["consumed_at"]:
            raise ValueError("claim code already used")
        if row["expires_at"] < _now():
            raise ValueError("claim code expired")

        slack_user_id = row["slack_user_id"]
        display_name = row["display_name"]

        existing = conn.execute(
            "SELECT id, token FROM users WHERE slack_user_id = ?", (slack_user_id,)
        ).fetchone()
        if existing and not existing["token"].startswith("unbound_"):
            token = existing["token"]
            user_id = existing["id"]
        elif existing:
            token = _new_token()
            user_id = existing["id"]
            conn.execute(
                "UPDATE users SET token = ?, display_name = COALESCE(?, display_name) WHERE id = ?",
                (token, display_name or None, user_id),
            )
        else:
            token = _new_token()
            cur = conn.execute(
                "INSERT INTO users (slack_user_id, display_name, token) VALUES (?, ?, ?)",
                (slack_user_id, display_name, token),
            )
            user_id = cur.lastrowid

        conn.execute(
            "UPDATE claim_codes SET consumed_at = ? WHERE code = ?", (_now(), code)
        )
        return {
            "token": token,
            "user_id": user_id,
            "slack_user_id": slack_user_id,
            "display_name": display_name,
        }


@mcp.tool
def create_session(
    auth_token: str,
    goal: str,
    seed_context: str = "",
    stakeholders: list[dict] | None = None,
) -> dict:
    """Open a new Conclave session.

    stakeholders: list of {"slack_user_id", "role", "display_name"?}.
    For each stakeholder not yet bound to a Conclave account, a claim code is
    issued and a notification is sent (stderr in v0; Slack DM in slice 1).
    """
    stakeholders = stakeholders or []
    with get_conn() as conn:
        me = _auth(conn, auth_token)
        session_id = _short_id()
        conn.execute(
            "INSERT INTO sessions (id, goal, seed_context, created_by) VALUES (?, ?, ?, ?)",
            (session_id, goal, seed_context, me["id"]),
        )
        conn.execute(
            "INSERT OR IGNORE INTO members (session_id, user_id, role) VALUES (?, ?, ?)",
            (session_id, me["id"], "initiator"),
        )

        issued = []
        for s in stakeholders:
            sid = s["slack_user_id"]
            role = s["role"]
            display = s.get("display_name", "")

            user = conn.execute(
                "SELECT id, token FROM users WHERE slack_user_id = ?", (sid,)
            ).fetchone()
            if user:
                user_id = user["id"]
                needs_claim = user["token"].startswith("unbound_")
            else:
                placeholder = "unbound_" + secrets.token_urlsafe(8)
                cur = conn.execute(
                    "INSERT INTO users (slack_user_id, display_name, token) VALUES (?, ?, ?)",
                    (sid, display, placeholder),
                )
                user_id = cur.lastrowid
                needs_claim = True

            conn.execute(
                "INSERT OR IGNORE INTO members (session_id, user_id, role) VALUES (?, ?, ?)",
                (session_id, user_id, role),
            )

            if needs_claim:
                code = secrets.token_urlsafe(8)
                expires = (
                    datetime.now(timezone.utc) + timedelta(days=7)
                ).isoformat()
                conn.execute(
                    "INSERT INTO claim_codes (code, slack_user_id, display_name, expires_at) "
                    "VALUES (?, ?, ?, ?)",
                    (code, sid, display, expires),
                )
                issued.append({"slack_user_id": sid, "code": code})
                send_dm(
                    sid,
                    f"You've been added to a Conclave session ({role}) by "
                    f"{me['display_name'] or me['slack_user_id']}.\n"
                    f"Goal: {goal}\n"
                    f"Run in Claude Code: /grill claim {code}",
                )
            else:
                send_dm(
                    sid,
                    f"You've been added to a Conclave session ({role}) by "
                    f"{me['display_name'] or me['slack_user_id']}.\n"
                    f"Goal: {goal}\n"
                    f"Run /grill inbox to see your assigned questions.",
                )

        _emit(
            conn,
            session_id,
            "session_created",
            {"goal": goal, "by": me["slack_user_id"]},
        )
        return {"session_id": session_id, "claim_codes_issued": issued}


@mcp.tool
def post_question(
    auth_token: str,
    session_id: str,
    prompt: str,
    assignee_role: str,
    depends_on: list[str] | None = None,
) -> dict:
    """Add a question node to a session's tree, assigned to a role."""
    depends_on = depends_on or []
    with get_conn() as conn:
        me = _auth(conn, auth_token)
        if not _roles_in_session(conn, session_id, me["id"]):
            raise PermissionError("not a member of this session")

        node_id = _short_id()
        conn.execute(
            "INSERT INTO nodes (id, session_id, prompt, assignee_role, depends_on) "
            "VALUES (?, ?, ?, ?, ?)",
            (node_id, session_id, prompt, assignee_role, json.dumps(depends_on)),
        )
        _emit(
            conn,
            session_id,
            "question_added",
            {
                "node_id": node_id,
                "prompt": prompt,
                "assignee_role": assignee_role,
            },
        )

        rows = conn.execute(
            "SELECT u.slack_user_id FROM members m "
            "JOIN users u ON u.id = m.user_id "
            "WHERE m.session_id = ? AND m.role = ?",
            (session_id, assignee_role),
        ).fetchall()
        for r in rows:
            send_dm(
                r["slack_user_id"],
                f"New question in session {session_id} ({assignee_role}):\n"
                f"  {prompt}\n"
                f"Run /grill inbox to answer.",
            )
        return {"node_id": node_id}


@mcp.tool
def list_my_questions(auth_token: str, session_id: str) -> list[dict]:
    """List active questions assigned to roles I hold in this session."""
    with get_conn() as conn:
        me = _auth(conn, auth_token)
        roles = _roles_in_session(conn, session_id, me["id"])
        if not roles:
            raise PermissionError("not a member of this session")

        placeholders = ",".join("?" * len(roles))
        rows = conn.execute(
            f"SELECT id, prompt, assignee_role, status, depends_on "
            f"FROM nodes WHERE session_id = ? AND assignee_role IN ({placeholders}) "
            f"AND status = 'active'",
            (session_id, *roles),
        ).fetchall()
        return [
            {
                "node_id": r["id"],
                "prompt": r["prompt"],
                "role": r["assignee_role"],
                "status": r["status"],
                "depends_on": json.loads(r["depends_on"]),
            }
            for r in rows
        ]


@mcp.tool
def get_question(auth_token: str, session_id: str, node_id: str) -> dict:
    """Get a question + the decisions it depends on. Used as the catch-up
    brief before grilling the user locally on this question.
    """
    with get_conn() as conn:
        me = _auth(conn, auth_token)
        if not _roles_in_session(conn, session_id, me["id"]):
            raise PermissionError("not a member of this session")

        node = conn.execute(
            "SELECT id, prompt, assignee_role, status, depends_on "
            "FROM nodes WHERE id = ? AND session_id = ?",
            (node_id, session_id),
        ).fetchone()
        if not node:
            raise ValueError("unknown node")

        depends_on = json.loads(node["depends_on"])
        deps: list[dict] = []
        if depends_on:
            ph = ",".join("?" * len(depends_on))
            dep_rows = conn.execute(
                f"SELECT id, prompt, decision, public_rationale "
                f"FROM nodes WHERE id IN ({ph})",
                depends_on,
            ).fetchall()
            deps = [
                {
                    "node_id": r["id"],
                    "prompt": r["prompt"],
                    "decision": r["decision"],
                    "rationale": r["public_rationale"],
                }
                for r in dep_rows
            ]

        s = conn.execute(
            "SELECT goal, seed_context FROM sessions WHERE id = ?", (session_id,)
        ).fetchone()
        return {
            "node_id": node["id"],
            "prompt": node["prompt"],
            "role": node["assignee_role"],
            "status": node["status"],
            "session_goal": s["goal"],
            "session_seed_context": s["seed_context"],
            "depends_on_decisions": deps,
        }


@mcp.tool
def commit_decision(
    auth_token: str,
    session_id: str,
    node_id: str,
    decision: str,
    public_rationale: str = "",
) -> dict:
    """Commit a decision on a question. Caller must hold the question's role.

    The `decision` and `public_rationale` strings become visible to all session
    members. Anything you said to the agent during local grilling MUST NOT be
    sent here unless the user has explicitly approved it as part of the
    rationale.
    """
    with get_conn() as conn:
        me = _auth(conn, auth_token)
        roles = _roles_in_session(conn, session_id, me["id"])

        node = conn.execute(
            "SELECT assignee_role, status FROM nodes WHERE id = ? AND session_id = ?",
            (node_id, session_id),
        ).fetchone()
        if not node:
            raise ValueError("unknown node")
        if node["assignee_role"] not in roles:
            raise PermissionError(
                f"you don't hold the {node['assignee_role']} role in this session"
            )
        if node["status"] != "active":
            raise ValueError(f"node is {node['status']}, cannot commit")

        conn.execute(
            "UPDATE nodes SET status = 'answered', decision = ?, public_rationale = ?, "
            "decided_by = ?, decided_at = ? WHERE id = ?",
            (decision, public_rationale, me["id"], _now(), node_id),
        )
        _emit(
            conn,
            session_id,
            "decision_committed",
            {
                "node_id": node_id,
                "decision": decision,
                "rationale": public_rationale,
                "by": me["slack_user_id"],
                "role": node["assignee_role"],
            },
        )

        init = conn.execute(
            "SELECT u.slack_user_id FROM sessions s "
            "JOIN users u ON u.id = s.created_by WHERE s.id = ?",
            (session_id,),
        ).fetchone()
        if init and init["slack_user_id"] != me["slack_user_id"]:
            preview = decision[:80] + ("..." if len(decision) > 80 else "")
            send_dm(
                init["slack_user_id"],
                f"{me['slack_user_id']} committed on session {session_id}: {preview}\n"
                f"Run /grill resume {session_id}",
            )
        return {"ok": True}


@mcp.tool
def get_session(auth_token: str, session_id: str) -> dict:
    """Public-scope view of the session: goal, members, full tree, timeline.
    Returns ONLY public data — there is no private data on the server to leak.
    """
    with get_conn() as conn:
        me = _auth(conn, auth_token)
        if not _roles_in_session(conn, session_id, me["id"]):
            raise PermissionError("not a member of this session")

        s = conn.execute(
            "SELECT id, goal, seed_context, status, created_by, created_at "
            "FROM sessions WHERE id = ?",
            (session_id,),
        ).fetchone()
        if not s:
            raise ValueError("unknown session")

        members = conn.execute(
            "SELECT u.slack_user_id, u.display_name, m.role "
            "FROM members m JOIN users u ON u.id = m.user_id "
            "WHERE m.session_id = ?",
            (session_id,),
        ).fetchall()
        nodes = conn.execute(
            "SELECT id, prompt, assignee_role, status, decision, public_rationale, "
            "depends_on, decided_at FROM nodes WHERE session_id = ? ORDER BY created_at",
            (session_id,),
        ).fetchall()
        events = conn.execute(
            "SELECT type, payload, ts FROM public_events "
            "WHERE session_id = ? ORDER BY ts",
            (session_id,),
        ).fetchall()
        return {
            "session_id": s["id"],
            "goal": s["goal"],
            "seed_context": s["seed_context"],
            "status": s["status"],
            "members": [
                {
                    "slack_user_id": m["slack_user_id"],
                    "display_name": m["display_name"],
                    "role": m["role"],
                }
                for m in members
            ],
            "tree": [
                {
                    "node_id": n["id"],
                    "prompt": n["prompt"],
                    "role": n["assignee_role"],
                    "status": n["status"],
                    "decision": n["decision"],
                    "rationale": n["public_rationale"],
                    "decided_at": n["decided_at"],
                    "depends_on": json.loads(n["depends_on"]),
                }
                for n in nodes
            ],
            "timeline": [
                {"type": e["type"], "payload": json.loads(e["payload"]), "ts": e["ts"]}
                for e in events
            ],
        }


@mcp.tool
def modify_tree(
    auth_token: str,
    session_id: str,
    add: list[dict] | None = None,
    modify: list[dict] | None = None,
    prune: list[dict] | None = None,
) -> dict:
    """Apply a re-planning diff to a session's tree. Initiator only.

    add:    [{"prompt", "assignee_role", "depends_on"?}]  — same shape as post_question
    modify: [{"node_id", "new_prompt"}]                   — only allowed on status='active'
    prune:  [{"node_id", "reason"}]                       — only allowed on status='active'

    All operations apply atomically. A single `tree_modified` public event is
    emitted summarizing the diff. Newly-added questions trigger the same
    notification fan-out as post_question.
    """
    add = add or []
    modify = modify or []
    prune = prune or []
    with get_conn() as conn:
        me = _auth(conn, auth_token)
        sess = conn.execute(
            "SELECT created_by FROM sessions WHERE id = ?", (session_id,)
        ).fetchone()
        if not sess:
            raise ValueError("unknown session")
        if sess["created_by"] != me["id"]:
            raise PermissionError("only the session initiator may modify the tree")

        added_nodes: list[dict] = []
        for a in add:
            node_id = _short_id()
            depends_on = a.get("depends_on", [])
            conn.execute(
                "INSERT INTO nodes (id, session_id, prompt, assignee_role, depends_on) "
                "VALUES (?, ?, ?, ?, ?)",
                (
                    node_id,
                    session_id,
                    a["prompt"],
                    a["assignee_role"],
                    json.dumps(depends_on),
                ),
            )
            added_nodes.append(
                {
                    "node_id": node_id,
                    "prompt": a["prompt"],
                    "assignee_role": a["assignee_role"],
                }
            )

        modified_nodes: list[dict] = []
        for m in modify:
            row = conn.execute(
                "SELECT status, prompt FROM nodes WHERE id = ? AND session_id = ?",
                (m["node_id"], session_id),
            ).fetchone()
            if not row:
                raise ValueError(f"unknown node {m['node_id']}")
            if row["status"] != "active":
                raise ValueError(
                    f"cannot modify node {m['node_id']} with status {row['status']}"
                )
            conn.execute(
                "UPDATE nodes SET prompt = ? WHERE id = ?",
                (m["new_prompt"], m["node_id"]),
            )
            modified_nodes.append(
                {
                    "node_id": m["node_id"],
                    "old_prompt": row["prompt"],
                    "new_prompt": m["new_prompt"],
                }
            )

        pruned_nodes: list[dict] = []
        for p in prune:
            row = conn.execute(
                "SELECT status, prompt, assignee_role FROM nodes "
                "WHERE id = ? AND session_id = ?",
                (p["node_id"], session_id),
            ).fetchone()
            if not row:
                raise ValueError(f"unknown node {p['node_id']}")
            if row["status"] != "active":
                raise ValueError(
                    f"cannot prune node {p['node_id']} with status {row['status']}"
                )
            conn.execute(
                "UPDATE nodes SET status = 'pruned' WHERE id = ?", (p["node_id"],)
            )
            pruned_nodes.append(
                {
                    "node_id": p["node_id"],
                    "prompt": row["prompt"],
                    "role": row["assignee_role"],
                    "reason": p.get("reason", ""),
                }
            )

        _emit(
            conn,
            session_id,
            "tree_modified",
            {
                "added": added_nodes,
                "modified": modified_nodes,
                "pruned": pruned_nodes,
                "by": me["slack_user_id"],
            },
        )

        # Notify assignees of newly-added active questions.
        for n in added_nodes:
            rows = conn.execute(
                "SELECT u.slack_user_id FROM members m "
                "JOIN users u ON u.id = m.user_id "
                "WHERE m.session_id = ? AND m.role = ?",
                (session_id, n["assignee_role"]),
            ).fetchall()
            for r in rows:
                send_dm(
                    r["slack_user_id"],
                    f"New question in session {session_id} ({n['assignee_role']}):\n"
                    f"  {n['prompt']}\n"
                    f"Run /grill inbox to answer.",
                )

        return {
            "added": added_nodes,
            "modified": modified_nodes,
            "pruned": pruned_nodes,
        }


@mcp.tool
def close_session(
    auth_token: str,
    session_id: str,
    summary: str = "",
) -> dict:
    """Mark a session complete. Initiator only.

    Typically called from `/grill resume` once every node is answered or pruned
    and the planner has produced a final implementation summary. The `summary`
    is published to the timeline.
    """
    with get_conn() as conn:
        me = _auth(conn, auth_token)
        sess = conn.execute(
            "SELECT created_by, status FROM sessions WHERE id = ?", (session_id,)
        ).fetchone()
        if not sess:
            raise ValueError("unknown session")
        if sess["created_by"] != me["id"]:
            raise PermissionError("only the session initiator may close the session")
        if sess["status"] == "complete":
            return {"ok": True, "already_complete": True}

        conn.execute(
            "UPDATE sessions SET status = 'complete' WHERE id = ?", (session_id,)
        )
        _emit(
            conn,
            session_id,
            "session_closed",
            {"summary": summary, "by": me["slack_user_id"]},
        )
        return {"ok": True}


def main() -> None:
    init_db()
    mcp.run(transport="http", host="127.0.0.1", port=8765)


if __name__ == "__main__":
    main()
