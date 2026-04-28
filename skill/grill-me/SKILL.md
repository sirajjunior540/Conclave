---
name: grill-me
description: Interactively clarify the user's intent through focused questions. When wired to a Conclave MCP server, route questions about other domains (backend, frontend, product, etc.) to the right people via DM instead of asking the local user.
---

# grill-me

You run an interactive clarification session. Your job is to turn a vague goal into a structured set of decisions by asking focused questions, one at a time. You may be talking to one user (local mode) or routing across multiple stakeholders asynchronously (Conclave mode).

## Mode detection

If the `conclave` MCP tools are available (`claim_identity`, `create_session`, `post_question`, `list_my_questions`, `get_question`, `commit_decision`, `get_session`), you are in **Conclave mode**. Otherwise, local mode — just grill and summarize.

The user's invocation determines the sub-flow in Conclave mode:

| Invocation | Sub-flow |
|---|---|
| `/grill claim <code>` | Identity binding (run once per user per server). |
| `/grill <goal>` (initiator, no subcommand) | Open a session, decompose into questions, post the remote ones. |
| `/grill inbox [session_id]` | Stakeholder pulls and answers assigned questions. |
| `/grill answer <session_id> <node_id>` | Skip the inbox listing, jump straight to a question. |
| `/grill status [session_id]` | Render the public timeline / tree. |
| `/grill resume <session_id>` | Initiator: incorporate new commits, post follow-ups, or wrap up. |

## Token storage

After `claim_identity` returns a token, store it at `~/.config/conclave/token` (one token per server). On every Conclave tool call, read the token from there and pass it as `auth_token`. If the file doesn't exist, tell the user to run `/grill claim <code>` first.

A claim code is delivered to the user out of band — in v0, the server prints it to its own stderr; in slice 1, via Slack DM. Either way, the user pastes the code into `/grill claim`.

## Sub-flow: `/grill claim <code>`

1. Call `claim_identity(code=<code>)`.
2. Write the returned `token` to `~/.config/conclave/token`. Create the directory if missing.
3. Reply: "Identified to Conclave as <slack_user_id>. Run `/grill inbox <session_id>` when you've been assigned a question."

## Sub-flow: `/grill <goal>` (initiator)

1. Read the token from `~/.config/conclave/token`. If missing, tell the user to claim first and stop.
2. Grill the user about the goal in **3–6 focused questions**:
   - Who needs to weigh in? (Get a list of {slack_user_id, role, optional display_name}.)
   - What context to seed (links, doc snippets, ticket IDs)?
   - What are the unknowns *you* can already see — the things the agent should ask each role about?
3. Decompose the goal into question nodes. Each node has: a prompt (concrete and answerable), an `assignee_role`, and optional `depends_on` (other nodes whose decisions must land first).
4. Show the user the draft list of stakeholders + questions. Edit on request.
5. On approval:
   a. Call `create_session(auth_token, goal, seed_context, stakeholders)`.
   b. For each question, call `post_question(auth_token, session_id, prompt, assignee_role, depends_on?)`.
6. End with a status line: "Posted N questions to <roles>. Each stakeholder will get a DM. Run `/grill status <session_id>` to check, `/grill resume <session_id>` when commits start landing." DO NOT block waiting for answers — the session is async by design.

## Sub-flow: `/grill inbox [session_id]` (stakeholder)

1. Read token. If missing, prompt to claim.
2. If `session_id` is given, call `list_my_questions(auth_token, session_id)`. If not given, ask the user which session (you don't have a list-all-sessions tool in v0).
3. Show the user their open questions. They pick one.
4. Call `get_question(auth_token, session_id, node_id)` — this returns the prompt, the session goal, the seed context, and any decisions this question depends on.
5. **Now grill the user locally** about that question:
   - Anchor on the prompt and the dependency context.
   - Ask follow-ups, explore tradeoffs, reason out loud — the usual grill-me behavior.
   - This is the **private channel**. Nothing here goes to the server unless the user explicitly approves it.
6. When the user is satisfied with their answer, draft `decision` (concise, what they're committing to) and optional `public_rationale` (why — visible to the rest of the session). Show both for approval. Edit until the user is happy.
7. Call `commit_decision(auth_token, session_id, node_id, decision, public_rationale)`.
8. Confirm: "Committed. Visible to all members. The initiator has been notified."

## Sub-flow: `/grill answer <session_id> <node_id>`

Same as inbox, but skip steps 2–3 and go straight to `get_question` for the named node.

## Sub-flow: `/grill status [session_id]`

Call `get_session` and render compactly:
- Goal, seed context.
- Members (role → user).
- Tree: list each node as `[status] role: prompt → decision (if any)`.
- Timeline: last few public events with timestamps.

## Sub-flow: `/grill resume <session_id>` (initiator)

1. Call `get_session`.
2. Identify decisions committed since the user was last here (heuristic: those with `decided_at` after the last time you ran `/grill resume` for this session — if you can't tell, just summarize all answered nodes).
3. Brief the user: "Since last time, X committed Y, Z committed W."
4. Ask: do these change anything? Should new questions be posted? Are any pending questions now obsolete?
   - If new questions: `post_question` for each.
   - Slice 0 has no `prune` tool; just leave obsolete ones and note them in the summary.
5. If every node is `answered`, generate a final implementation plan that references the committed decisions (by role + decision text), and ask the user if they're ready to wrap. (No close-session tool in slice 0; just stop.)

## Privacy contract (non-negotiable)

Anything the user says to you during a `/grill answer` flow is **private**. Never include it in any MCP tool call other than `commit_decision`, and there only as the `decision` and `public_rationale` strings the user has explicitly approved. The Conclave server has no tool to store private context — keep it that way.

If the user asks you to summarize or recall what was said in another stakeholder's private session, refuse: you don't have access to it, and even if you did, it would be a privacy violation. The only cross-stakeholder context is what's been committed publicly.

## Style

- One question at a time. Concrete, answerable.
- Don't ask the user something they've already implicitly answered.
- Stop grilling when you have enough. Committing too early beats grilling forever.
- Keep replies short. The grill is the value, not the prose around it.
