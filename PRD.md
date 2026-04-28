# Conclave — PRD

**One-liner:** A shared agent session where multiple domain owners answer a branching decision tree asynchronously. Each owner deliberates *privately* with the agent, but only their *committed answer* is visible to the rest of the session and shapes what comes next.

**Status:** Draft v0.2 — 2026-04-28 (reframed around Claude Code + MCP)
**Author:** sirajjunior@gmail.com

---

## 1. Problem

When an autonomous agent (or a human lead) wants to ship a non-trivial change across a codebase or organization — say, "add multi-tenant billing" — the decision is rarely owned by one person. Backend, frontend, product, security, infra, and design each hold a piece of context. Today this gets resolved by:

- Round-robin Slack threads where context fragments and people interrupt each other.
- Synchronous meetings that are hard to schedule across time zones.
- Long Notion docs where comments pile up but nobody knows what's "decided."

The cost is not just time — it's **decision drift**: by the time the last stakeholder answers, the question they're answering is already stale because earlier answers have changed the shape of the problem.

## 2. Concept

Conclave models the work as a **branching decision tree** owned by an agent and answered asynchronously by multiple stakeholders.

Three things make Conclave different from "ask people in a Slack thread":

1. **Per-stakeholder private channel.** Each user gets a 1:1 sub-conversation with the agent to clarify their question. Their messages, half-formed thoughts, "wait, what does X mean?" are *not* seen by other participants. Only the **committed decision** is published.
2. **Decisions reshape the tree.** When a user commits an answer, the agent re-plans the remainder of the tree. New questions may appear for other users; some branches may be pruned.
3. **Asynchronous by design.** The session has no concept of "everyone online now." Stakeholders are notified when a question is assigned to them, work on it on their own time, and the tree advances as decisions land.

### 2.1 The agent is Claude Code; Conclave is an MCP server

In practice, the agent is **Claude Code** — the initiator's Claude Code instance, equipped with an interactive clarification skill (a "grill me" pattern) that turns rough goals into concrete, role-targeted questions. Conclave itself ships as an **MCP server** that Claude Code connects to. Posting a question, polling for assigned questions, and recording commits are all *MCP tool calls*.

This reframing matters for three reasons:

- **No bespoke agent runtime.** Claude Code is the agent. The MCP server is dumb infrastructure: sessions, tree state, timeline, notifications. No LLM lives inside it.
- **Privacy becomes a property of the architecture, not a discipline in code.** Each stakeholder's private clarification happens inside *their own* Claude Code session, on their own machine, against the model they configured. That conversation never enters the Conclave server. The server has nothing to leak because it never received it.
- **Stakeholders use the tool they already have.** A backend lead who already runs Claude Code just gets a `conclave` MCP server in their settings, runs a slash command (e.g. `/conclave inbox`), and answers their questions. No new app to install. Stakeholders without Claude Code use a thin web UI that talks to the same server (see §7.4).

The "grill me" skill is what runs on each side of every interaction:

- *Initiator side:* grills the initiator about the goal, drafts the initial tree, posts each question via MCP.
- *Stakeholder side:* fetches their assigned question via MCP, grills the stakeholder locally, submits a commit via MCP.

## 3. Users

| Role | Persona | What they do in Conclave |
|---|---|---|
| **Initiator** | The person (or agent) who opens a Conclave. Often a tech lead or PM. | Defines the goal, seeds context, designates initial stakeholders, and watches the tree fill in. |
| **Stakeholder** | A domain owner — backend lead, frontend lead, designer, security, etc. | Receives questions assigned to their role, optionally clarifies privately with the agent, then commits a decision. |
| **Observer** | Someone who needs visibility but doesn't decide. | Read-only view of the public tree; cannot see private deliberations. |
| **Agent** | Claude Code with a grill-me-style clarification skill, connected to the Conclave MCP server. There is no central agent — every participant runs their own Claude Code (or uses the web UI), and "the agent" at any moment is whichever instance is currently acting. | Grills its user, posts/answers questions through MCP tool calls, runs the re-plan step after a commit. |

## 4. Core Mechanics

### 4.1 The Session Object

A **Conclave session** holds:

- **Goal** — what the initiator wants to accomplish, plus any seed context (linked repo, design doc, ticket).
- **Stakeholder roster** — `{ user → role }` mapping. A user can hold multiple roles.
- **Decision tree** — directed acyclic graph of `Question` nodes. Each node has:
  - `assignee_role` — which role must answer.
  - `prompt` — the question text the agent generated.
  - `depends_on` — parent decisions this question is contingent on.
  - `status` — `pending | active | answered | pruned`.
  - `decision` — the committed answer (only present when `status = answered`).
- **Public timeline** — append-only log of committed decisions, visible to every member.
- **Private threads** — one-per-user transcript of agent ↔ that user clarification messages. *Not visible to other users, ever.*

### 4.2 The Public/Private Boundary

This is the central design contract.

| Visible to all members | Visible only to the user (and the agent) |
|---|---|
| The question being asked of a role | The user's draft answers / "what if I answered X?" probes |
| The committed decision and its rationale (if user opts to share) | Clarifying questions the user asks the agent |
| The shape of the tree (which questions exist, what's pending) | The agent's tailored explanations to that user |
| Who is assigned to what | Anything the user marks "private note" |

The agent treats private context as **inputs to its reasoning** but must not surface it in any other user's view, public summary, or downstream prompt unless the user explicitly publishes it.

In the Claude Code + MCP architecture this split has a clean physical realization: **private context lives only inside each user's local Claude Code session and is never sent to the Conclave server**. The MCP server has no `private_messages` table because there are no private messages to store. "Don't leak private context" stops being an enforcement problem and becomes a property of the deployment shape — the bytes literally never cross the wire.

### 4.3 Decision Tree Lifecycle

1. **Plan.** Initiator submits goal + roster. Agent generates an initial tree of ~5–10 questions, assigning each to the most relevant role.
2. **Notify.** Stakeholders are paged (email / Slack / in-app) when a question becomes `active` for their role.
3. **Deliberate (private).** Stakeholder enters their private thread. They can:
   - Ask the agent to explain context, tradeoffs, or implications.
   - Run "what-if" probes — "if I say A, what happens to frontend's question?" — without committing.
   - Drop private notes for their own future reference.
4. **Commit.** Stakeholder publishes a decision. Optionally attaches a public rationale (the agent can draft one from the private thread on request, with the user's review).
5. **Re-plan.** Agent updates the tree: marks dependent nodes ready, generates new nodes if needed, prunes obsolete branches. Other stakeholders are re-notified.
6. **Converge.** Tree is complete when every leaf is `answered` or `pruned`. Agent emits a final implementation plan or hand-off doc.

### 4.4 Re-planning Rules

- A commit can **add** new questions, **modify** pending question text, or **prune** branches. It must never silently change an *already-committed* decision.
- If a new commit invalidates an earlier decision (e.g., backend's choice now contradicts product's), the agent flags a **conflict**, opens a reconciliation question assigned to both parties, and pauses dependent branches.
- Pruned branches are visible in the timeline as "no longer relevant" with a one-line reason — never silently deleted.

## 5. Functional Requirements

### 5.1 Must-have (MVP)

- **F1.** Create a session with a goal, seed context, and a stakeholder roster (role → user mapping).
- **F2.** Agent generates an initial decision tree of N questions, each tagged with assignee role.
- **F3.** Stakeholders see only questions assigned to their role(s), in a personal inbox.
- **F4.** Each question opens a **private thread** between the user and the agent. Messages in this thread are not retrievable by any other user or surfaced in others' contexts.
- **F5.** A user can **commit** a decision from their private thread. Commit is irrevocable without an explicit "reopen" by the initiator.
- **F6.** On commit, agent re-plans: may add/modify/prune downstream nodes. Other stakeholders are notified of new active questions.
- **F7.** Public timeline shows every commit (question + decision + optional rationale + timestamp + author) to all members.
- **F8.** Session-complete state generates a structured summary (e.g., implementation plan) from all decisions.

### 5.2 Should-have

- **S1.** "What-if" mode — user can simulate an answer and see a preview of the resulting tree without committing.
- **S2.** Conflict detection between commits, with a generated reconciliation question.
- **S3.** Mentions / handoff — a stakeholder can punt their question to another user with a note.
- **S4.** Audit log — the private threads are stored encrypted-at-rest and recoverable only by the user and (optionally) by a designated admin under an explicit policy.
- **S5.** Templates — reusable session shapes ("new feature," "incident postmortem," "architecture change") with pre-baked role rosters and seed questions.

### 5.3 Won't-have (v1)

- Real-time multi-user editing on the same node.
- Cross-session memory (each Conclave is self-contained).
- Voice / video.

## 6. UX Flows

### 6.1 Initiator opens a session

1. Picks template or starts blank.
2. Writes the goal (free text) and attaches context (repo links, docs).
3. Adds stakeholders by email + role tag.
4. Reviews the agent-generated initial tree, can accept / edit / regenerate.
5. Hits "start" — notifications fire.

### 6.2 Stakeholder answers a question

1. Receives notification with one-line summary of the goal + their assigned question.
2. Clicks in — lands in their private thread, agent has already drafted a brief catching them up on relevant prior commits.
3. Asks clarifying questions, optionally probes "what-if."
4. Drafts decision; agent offers to draft a public rationale.
5. Clicks **Commit**. Sees confirmation: "This will be visible to N members. Your private thread will not."
6. Returns to inbox; sees other questions if assigned.

### 6.3 Anyone watches the tree

- Reads the public timeline, latest commits at top.
- Hovers a node to see its question, status, assignee.
- Cannot expand into private threads they don't own.

## 7. Architecture

Conclave ships as a single **MCP server** plus a thin web UI for stakeholders who don't run Claude Code. The agent — the thing that grills, plans, and re-plans — is *whichever Claude Code instance is currently acting*. The server holds no model and runs no LLM.

### 7.1 Topology

```
   Initiator's machine                 Backend lead's machine
  ┌────────────────────┐              ┌────────────────────┐
  │ Claude Code        │              │ Claude Code        │
  │  + grill-me skill  │              │  + grill-me skill  │
  │  (private context  │              │  (private context  │
  │   stays here)      │              │   stays here)      │
  └─────────┬──────────┘              └─────────┬──────────┘
            │ MCP                               │ MCP
            ▼                                   ▼
       ┌────────────────────────────────────────────┐
       │           Conclave MCP server              │
       │  sessions · tree · public timeline · auth  │
       │       (no LLM, no private messages)        │
       └──────────┬─────────────────────────────────┘
                  │ HTTP + SSE
                  ▼
          ┌──────────────────┐
          │  Web UI          │  ← stakeholders without
          │  (Next.js)       │     Claude Code
          └──────────────────┘
```

### 7.2 Server-side data model

Note what's *missing* — there are no tables for private threads or private messages.

```
Session(id, goal, seed_context, status, created_by, created_at)
Member(session_id, user_id, roles[])
Node(id, session_id, parent_ids[], assignee_role, prompt, status, decision_json, decided_by, decided_at)
PublicEvent(session_id, type, payload, ts)   # question_added, decision_committed, branch_pruned, conflict_raised
```

Auth is per-MCP-connection: the server resolves the connection to a `user_id`, and tools authorize against that.

### 7.3 The privacy boundary, structurally

Two scopes still exist, but they map to *machines* rather than to filters in code:

- `public_scope` lives on the Conclave server. Sent to anyone who can read the session.
- `user_scope` for user U lives on user U's machine — inside their Claude Code conversation. There is no "user_scope" row in any server database.

Property: there is no MCP tool that returns another user's private context, because no MCP tool stores private context to begin with. A user can choose to publish part of their private thread (as a rationale on a commit), but that requires their explicit action.

### 7.4 Re-planning loop

On each commit:

1. The committing client calls `conclave_commit_decision`. The server persists the `Node` update and emits a `PublicEvent`.
2. The server designates a **planner instance** — by default, the initiator's Claude Code, but configurable. The planner receives the event via the MCP subscription stream.
3. The planner runs locally in `public_scope` only (it pulls the current tree + new commit via `conclave_get_session`). It produces a tree diff: `add`, `modify`, `prune`, `conflict`.
4. The planner submits the diff via `conclave_modify_tree`. The server applies it atomically and notifies newly-active assignees.
5. If `conflict`, the diff includes a reconciliation node assigned to both involved roles; dependents pause.

If the initiator's Claude Code isn't online when a commit lands, the server queues the re-plan event. Re-plans aren't required to be instantaneous; the tree just sits in its current state until the planner reconnects. (For a hosted version, you can run a managed planner agent, but it's not required for v1.)

### 7.5 MCP tool spec

The Conclave MCP server exposes these tools (sketch — names will firm up during implementation):

```
conclave_create_session(goal, seed_context, stakeholders[{email, role}])
    → { session_id }

conclave_get_session(session_id)
    → { goal, members, public_tree, timeline }

conclave_post_question(session_id, prompt, assignee_role, depends_on[])
    → { node_id }
    # used by the planner instance during initial tree generation

conclave_list_my_questions(session_id)
    → [{ node_id, prompt, depends_on_decisions, status }]
    # filtered to the calling user's role(s)

conclave_get_question(session_id, node_id)
    → { prompt, depends_on_decisions, related_public_commits }
    # the catch-up brief a stakeholder needs to grill against locally

conclave_commit_decision(session_id, node_id, decision, public_rationale?)
    → { ok, replan_event_id }

conclave_modify_tree(session_id, diff: { add[], modify[], prune[], conflict[] })
    → { ok }
    # restricted to the planner instance for this session

conclave_subscribe(session_id)
    → stream of PublicEvent  # delivered as MCP notifications
```

Design notes:

- **No private-message tools, by construction.** There is intentionally no `conclave_save_private_note` or `conclave_get_thread_history`. If a stakeholder wants their grilling preserved across sessions, that's their local Claude Code's problem, not Conclave's.
- **`commit_decision` is the only tool that reveals user intent to others.** It's the single chokepoint, easy to audit and to surround with confirmation UX ("This will be visible to N members").
- **`modify_tree` is privileged.** Only the designated planner instance for the session may call it. Token / capability handed out by the server when an instance claims the planner role.
- **Subscriptions** drive the stakeholder UX: when a question becomes active for you, your client sees it appear in `list_my_questions` and surfaces it (notification, slash command output, web inbox).

### 7.6 How questions actually reach stakeholders (delivery)

MCP is a client-initiated protocol — the Conclave server can't push to a Claude Code instance that isn't running. Delivery is therefore a small system, not a single mechanism. Four channels, mixed and matched:

| # | Channel | When it works | Latency | v1? |
|---|---|---|---|---|
| 1 | **Live MCP notification.** Server emits a notification over the open MCP connection; the Conclave skill in Claude Code surfaces it. | Stakeholder's Claude Code is open and connected to the Conclave MCP server. | Seconds. | Polish |
| 2 | **Out-of-band ping → pull.** Server sends email / Slack / OS push. User opens Claude Code, runs `/conclave inbox`, which calls `conclave_list_my_questions`. | Always — the ping doesn't depend on Claude Code. | Whenever the user next checks. | **Yes — baseline** |
| 3 | **Background agent on the user's side.** A long-running Claude Agent SDK process (or daemon) keeps an MCP subscription open and raises an OS notification on arrival. | User has installed the helper. | Seconds. | Later |
| 4 | **Web UI fallback.** Email link → browser inbox served by the same Conclave server via HTTP. | Stakeholder has no Claude Code, or is on mobile. | Whenever they click the link. | **Yes — required for non-CLI users** |

**v1 picks #2 + #4.** They cover every stakeholder reliably and don't depend on Claude Code being open. #1 is added as a polish for users who happen to have Claude Code running — nice when it works, but never the *only* delivery path. #3 is a future enhancement.

A consequence: a question posted now is "delivered" to the recipient at *some unknown later time* when they sit down and pull. The tree's pace is bounded by stakeholders checking their inbox, not by the server's push. That's actually fine for an asynchronous decision tool — but it's worth flagging that "real-time" is not a property Conclave provides or needs.

### 7.7 The Claude Code skill side

On each user's machine, a small **grill-me-style Claude Code skill** is the human-facing surface:

- *Initiator:* `/conclave start` → grills initiator about the goal, drafts an initial tree, calls `conclave_create_session` + `conclave_post_question` for each node.
- *Stakeholder:* `/conclave inbox` → calls `conclave_list_my_questions`. Picking a question opens a local grilling session backed by `conclave_get_question`'s brief. When the user is satisfied, the skill calls `conclave_commit_decision` with their answer (and an optional rationale the skill can draft from the local conversation, with the user's review before sending).
- *Anyone:* `/conclave status` → public timeline / tree summary.

The skill's job is mostly to keep the Claude Code conversation focused: it loads only public context from the server, grills the user against it, and converts the resulting decision into a tool call. The model picks which Claude variant to use locally is the user's choice — Conclave doesn't dictate it.

### 7.8 Suggested stack

- **MCP server:** Python with FastMCP, or Node with `@modelcontextprotocol/sdk`. Postgres for sessions/tree/timeline. Postgres `LISTEN/NOTIFY` (or Redis pub/sub) for the event stream feeding `conclave_subscribe`.
- **Web UI for non-Claude-Code stakeholders:** Next.js, talks to the same server via REST + SSE, runs an in-browser grilling chat against the Anthropic SDK. The browser session holds the private grilling locally; only the commit hits the server.
- **Auth:** OIDC for both the MCP server and the web UI; per-session role assignment by the initiator. Each MCP connection authenticates as a user.
- **Notifications:** email + Slack for "you have a new assigned question." User-configurable preferences per session.
- **Models (advisory, not enforced):** Sonnet 4.6 for tree-shaping and re-planning, Haiku 4.5 for the cheaper grilling turns. Anthropic prompt caching keyed on `session_id` to keep the public-scope context warm across re-plans.

## 8. Risks & Open Questions

1. **Privacy leakage through summaries.** If the agent ever summarizes "what's been decided so far" using a user-scope context, it could leak that user's private thoughts into the public summary. → Mitigation: the summary endpoint is hard-wired to public scope only; never called from inside a private thread.
2. **Latency of re-planning.** If every commit triggers a full re-plan and there are 6 stakeholders, the tree may churn faster than people can read it. → Consider batching commits within a window, or letting the initiator gate re-plans.
3. **What if a stakeholder is wrong?** Need a clean "reopen" affordance — initiator can unlock a committed node, with the prior decision preserved as history.
4. **Role ambiguity.** When two people share a role (two backend leads), who owns the question? → Either pick one explicitly per node, or allow co-assignment with explicit "either may decide" or "both must agree."
5. **Agent quality on re-planning.** Bad re-plans erode trust fast. → Always show the diff before applying; let the initiator preview/approve large reshapings (configurable threshold).
6. **Auditability vs. privacy.** Are private threads truly private from admins, or recoverable under an org policy? → Make this an explicit org-level setting, surfaced to users at commit time.
7. **What stops this from being a meeting in slow motion?** The decision tree itself is the value — it forces structure. If users just chat in private threads forever and never commit, nothing advances. Possibly a soft deadline per node.

## 9. Success Metrics

- **Time-to-converge** — hours from session-open to all-decided. Target: 1/3 of equivalent meeting + thread baseline.
- **Decisions per session** — average tree depth × breadth actually answered (not pruned).
- **Reopen rate** — % of committed decisions that get reopened. High = poor question quality or premature commits.
- **Private-thread engagement** — % of commits preceded by ≥1 private clarification turn. Indicates the private channel is providing real value vs. being skipped.
- **NPS by role** — do backend leads find this less annoying than Slack threads?

## 10. MVP Cut

Smallest version that proves the core idea:

- One session at a time per user.
- Manual stakeholder roster (no SSO directory).
- Initial tree generation + private threads + commits + re-planning. No what-if, no conflict detection, no templates.
- Email notifications only.
- Public timeline as a flat list (DAG view comes later).

If the MVP shows that (a) people actually use the private channel and (b) re-planning feels useful rather than chaotic, the rest of the roadmap is worth building.

---

## Appendix A — Worked example

**Goal:** "Add per-tenant usage-based billing to the SaaS app."

Initial tree (agent-generated):

- Q1 → Product: "What is the unit of metering — API calls, seats, GB-months?"
- Q2 → Backend: "Where does the meter live — gateway, service-level, batch ETL?"
- Q3 → Frontend: "Do customers see live usage or end-of-period only?"
- Q4 → Security: "Is per-tenant usage data PII for any of our customer segments?"

Product commits Q1 = "API calls, billed monthly."
→ Re-plan: Q2 stays. Q3 narrows to "do we show running monthly count or per-call?". New Q5 → Backend: "What's our retention policy on per-call records?" (depends on Q4).

Backend opens Q2 in their private thread, asks the agent: "If I pick gateway, how does that interact with our existing rate-limit middleware?" — agent explains, backend probes "what if I pick service-level instead?" with no commitment. Eventually commits "gateway, with sampling above 10k/hr."

Etc. Final output: an implementation plan referencing every committed decision, ready to hand to engineering.
