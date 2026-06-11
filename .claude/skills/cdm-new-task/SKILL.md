---
name: cdm-new-task
description: Create one or more well-formed tasks on the production CDM Jira board (project CDM, https://delfidiagnostics.atlassian.net/jira/software/projects/CDM/boards/32) following the team's 2026 taxonomy. Use whenever someone wants to file/open/create a new CDM ticket or task. Takes a free-text description, decides whether it's one Task, several independent Tasks, or one Task broken into Subtasks, classifies each into the right Epic + cat-/proj-/study- labels, drafts descriptions, infers assignees, confirms the full plan, then creates everything via the Atlassian Rovo connector.
---

# Create a CDM task (2026 taxonomy)

Turn a free-text task description into one or more correctly-classified CDM tickets and put them on the board. Every CDM **Task** answers four questions — **Epic** (which 2026 goal), **cat-** (kind of work), **proj-** (system/workstream), **study-** (which study) — plus a clear summary, a drafted description, and an assignee. A request isn't always a single ticket: it may be several independent Tasks, or one Task broken into Subtasks — see "Decide the shape" (step 2) below.

## Fixed facts (production CDM)

- **Cloud ID:** `f33c9366-7e25-468f-8a01-6c69d59e79e4`
- **Project key:** `CDM` · **Board:** 32 · **Issue type:** `Task` for new work (use `Subtask` only to break down a single Task — see "Decide the shape" in step 2 and the "Subtasks" section below)
- **Default status:** `To Do` (no transition needed on create)
- Use the **Atlassian Rovo** connector tools (`mcp__claude_ai_Atlassian_Rovo__*`).

### The 6 epics (pick exactly one)

| Epic name | CDM key | Use for |
|---|---|---|
| CASCADE (L201) Readout | `CDM-676` | L201 / CASCADE readout work |
| IVD Lung PMA Submission | `CDM-677` | PMA package — clinical / analytical / manufacturing |
| 4ITLR Readout | `CDM-679` | 4ITLR readout |
| Reimbursement & Clinical Evidence | `CDM-707` | CU readout, CED, evidence publications |
| Departmental Ops | `CDM-708` | service / BAU / infra / recurring — anything not tied to a 2026 goal |
| Pre-2026 Legacy | `CDM-709` | archive only — **never file new work here** |

Epic keys are stable post-migration, but if a create fails on a bad parent, re-resolve them with
`searchJiraIssuesUsingJql` → `project = CDM AND issuetype = Epic ORDER BY key ASC` and use the live keys.

### Labels — three required namespaces

Every ticket needs **all three** kinds. Labels carry the prefix literally (e.g. `cat-programming`).

- **`cat-*`** — *kind of work*, **exactly 1**:
  - `cat-programming` — closes when code is merged (scripts, pipelines, automation, repos, notebooks)
  - `cat-reporting` — closes when a report / listing / dashboard is delivered or refreshed (anything humans consume)
  - `cat-operations` — everything else (EDC config, UAT, validation, SOPs, training, planning, vendor mgmt, audit, threshold-release activities)
  - *Close-the-ticket test:* ask "what makes this done?" — merged code → programming; a delivered artifact people read → reporting; otherwise operations.
- **`proj-*`** — *system / workstream*, **1 or more** (stack freely when it spans systems):
  - `proj-cpt` — Clean Patient Tracker (any study)
  - `proj-dqr` — standalone DQR listings *not inside CPT*, and the DQR platform itself
  - `proj-edc` — EDC builds, releases, edit checks, page configuration, edit-check logic
  - `proj-metrics` — study dashboards, KPI/metric reports, EDC page metrics, case-tracker dashboards (Clario, Suspicious Nodule, etc.)
  - `proj-cancer-review` — cancer adjudication & review reporting (CDR is the L201 instance)
  - `proj-pipeline` — data/processing pipelines — SURF read-ins, ADaM derivations, analysis stitchers
  - `proj-cro` — CRO interfacing & external reporting (PPD is the L201 CRO)
  - `proj-sample` — sample tracking, recollection, processing operations
  - `proj-data-release` — clinical data threshold/snapshot release governance (CDCP, cleaning plans, snapshot sign-offs, release approval forms, S3 setup tied to a formal release)
  - `proj-cv` — Clinical Validation workstream (CV data sets, CV readouts)
  - `proj-av` — Analytical Validation workstream (AV data sets, AV readouts)
  - `proj-other` — catch-all for operational/admin/training with no data-workstream fit (SOP authoring, hiring, training, team-process). Use only when nothing else fits.
- **`study-*`** — *study*, **exactly 1**:
  - `study-l101` (DELFI-L101) · `study-l201` (DELFI-L201 / CASCADE) · `study-l301` (DELFI-L301) · `study-4itlr` (4ITLR) · `study-onom` (Onom) · `study-cross` (infra/tooling or spans studies — the default when no single study applies)

How they fit together: **Epic** = *why* (the goal) · `cat-` = *what kind* · `proj-` = *where in the stack* · `study-` = *whose data*.

## Workflow

1. **Read the request.** Take the user's free-text description. Pull out: what the work is, what makes it done, any system/study/vendor names (e.g. CPT, DQR, EDC, Clario, PPD, CDR, SURF, L201, 4ITLR), and any named owner. Note any wording that signals **more than one piece of work** — lists, "and then", "also", multiple distinct deliverables, or steps that happen in sequence.

2. **Decide the shape — one Task, several Tasks, or one Task + Subtasks.** Default to a **single Task**; only split when the work clearly isn't one closeable unit.
   - **Single Task** (default) — one cohesive piece of work with one "done" condition.
   - **Multiple independent Tasks** — the request bundles distinct pieces that close separately, could be owned or scheduled independently, or fall under **different Epics, studies, or `cat-`/`proj-` classifications**. Each becomes its own fully-classified Task under its own Epic. Prefer this whenever the pieces don't share a single deliverable — it's the on-taxonomy shape and the thing the migration existed to enforce.
   - **One Task + Subtasks** — there's a single deliverable that closes as a unit but is worth tracking as a checklist of steps. The parent Task carries the full Epic + `cat-`/`proj-`/`study-` classification; each Subtask is a step that lives and dies with the parent (see "Subtasks" below).

   *Tie-breaker:* if the pieces would each carry a **different Epic, study, or `cat-`**, they are **separate Tasks** — a Subtask can't hold its own Epic, so it must match its parent's goal/study. If they share one Epic and study and close together, use Subtasks (or separate Tasks if each is independently closeable on its own timeline). When it's genuinely 50/50, state your read in one line and ask the user which shape they want before drafting. Don't silently fan a vague request into many tickets — when unsure how finely to split, ask.

3. **Classify each Task** using the tables above. Do this for the single Task, or for *every* Task in a split:
   - **Epic** — which 2026 goal does it serve? If it's pure service/BAU/infra with no goal tie, use Departmental Ops. Never Pre-2026 Legacy for new work.
   - **`cat-`** — apply the close-the-ticket test (pick exactly one).
   - **`proj-`** — one or more; stack when it spans systems. Case-tracker/dashboard work → `proj-metrics`.
   - **`study-`** — exactly one; `study-cross` when not study-specific.
   - Form a concise, specific **summary** line (no key prefix; Jira assigns the key).
   - **For a Task + Subtasks shape:** classify only the parent Task against the Epic. Each **Subtask** still gets its own `cat-`/`proj-`/`study-` labels (often the same as the parent, but set them per the subtask's actual work) and **no Epic** — it inherits the parent Task's Epic transitively.

4. **Draft a description for each Task** (and a short body or just a clear summary for each Subtask), then confirm. Write a short structured body in markdown:
   - **Context** — 1–2 sentences on why / background.
   - **Scope / what's involved** — bullets of the concrete work, when known.
   - **Acceptance / done when** — what makes the ticket closeable (mirror the `cat-` close test).
   Keep it to what the user actually gave you — don't invent specifics. If input is thin, keep the body short rather than padding it. Subtasks usually need only a one-line summary; add a body only if there's real detail.

5. **Infer the assignee** (per Task; a Subtask defaults to its parent Task's assignee unless the user says otherwise).
   - If the user named an owner, resolve them with `lookupJiraAccountId` (exact display-name match; if the search is fuzzy/ambiguous, ask).
   - If the user implies it's their own work ("I need to…", "I'm going to…") or names no one, default to the current user via `atlassianUserInfo`.
   - If ownership is genuinely unclear (e.g. "someone should…"), **ask** who owns it, offering the current user as the default and "leave unassigned" as an option.
   - Known inactive accounts cannot be assigned (Jira rejects deactivated users): Erica Peters, Lee Ming Sun, Vincent Puga-Aragon, Clarice Grant, Pavlova Nalley, Spencer King. If one of these is named, flag it and leave unassigned or pick a live owner.

6. **Ask clarifying questions only when genuinely needed** — a required field is ambiguous (two epics fit equally, the study is unclear, programming vs operations), *or* the shape is 50/50 (one Task vs several vs subtasks). Ask the minimum needed (prefer a single grouped question). If the request and its shape are unambiguous, skip straight to confirmation.

7. **Always confirm the full plan before creating.** Show everything you're about to create and wait for an explicit OK.

   **Single Task** — show one block:
   ```
   Project:   CDM (board 32)
   Type:      Task
   Epic:      <name> (<key>)
   Summary:   <summary>
   Labels:    cat-… , proj-… [, proj-…] , study-…
   Assignee:  <name | Unassigned>
   Status:    To Do
   Description:
   <drafted body>
   ```

   **Multiple independent Tasks** — number them and show one block each (each with its own Epic + labels + assignee). Lead with a one-line summary of why you split it this way.

   **One Task + Subtasks** — show the parent Task block (with Epic + labels), then a `Subtasks:` list, each with its own Summary + Labels + Assignee and **no Epic line** (it inherits the parent's). For example:
   ```
   Parent Task:
     Epic:     <name> (<key>)
     Summary:  <summary>
     Labels:   cat-… , proj-… , study-…
     Assignee: <name | Unassigned>
   Subtasks (parent = the Task above, no own epic):
     1. <summary>  | cat-… , proj-… , study-…  | <assignee>
     2. <summary>  | cat-… , proj-… , study-…  | <assignee>
   ```
   Apply any edits the user requests and re-confirm if they changed a required field or the shape.

8. **Create everything** with `createJiraIssue`, in dependency order:

   For each **Task** (single, or each in a split):
   ```
   cloudId:        f33c9366-7e25-468f-8a01-6c69d59e79e4
   projectKey:     CDM
   issueTypeName:  Task
   summary:        <summary>
   description:    <drafted body>
   parent:         <epic key, e.g. CDM-708>      # epic is the parent for Tasks
   assignee_account_id: <accountId>              # omit entirely if unassigned
   additional_fields: { "labels": ["cat-…","proj-…","study-…"] }
   ```

   For a **Task + Subtasks** shape, create the **parent Task first**, capture the key it returns, then create each Subtask:
   ```
   cloudId:        f33c9366-7e25-468f-8a01-6c69d59e79e4
   projectKey:     CDM
   issueTypeName:  Subtask
   summary:        <subtask summary>
   parent:         <PARENT TASK key, e.g. CDM-742>   # NOT the epic — the Task you just created
   assignee_account_id: <accountId>                  # omit if unassigned
   additional_fields: { "labels": ["cat-…","proj-…","study-…"] }   # no epic field
   ```
   Notes:
   - **Labels go in `additional_fields.labels`** as a flat string array — they have no top-level parameter.
   - **For a Task, the epic is set via `parent`** (CDM is team-managed; the epic *is* the Task's parent). For a **Subtask, `parent` is the Task key, never an epic** — Jira rejects a Subtask under an Epic (`400 issue type constraint`), and a Subtask carries no epic field of its own.
   - **Create the parent Task before its Subtasks** so you have the real key to parent them to. Independent Tasks have no ordering constraint.
   - CDM's subtask type is named `Subtask` (verified — id `10116`, `subtask: true`). If a create ever fails on the type name, re-resolve the live name with `getJiraProjectIssueTypesMetadata` and retry.
   - Leave status alone — new issues start in `To Do`.

9. **Report the results** — list every created key with its link (`https://delfidiagnostics.atlassian.net/browse/<KEY>`) and the epic each Task landed under (group Subtasks under their parent Task).
   - **⚠️ Remind the user to move Tasks to the board.** CDM is a team-managed Kanban project, so each new **Task** lands in the **Kanban backlog** (status `To Do`), *not* directly on board 32 — and neither this skill nor the Rovo connector can move it (the Agile board API isn't exposed). Subtasks follow their parent and aren't separate board cards. Always end by telling the user, in plain terms:
     > Created **<KEY(s)>** — the Task(s) are in the **Backlog**. Open board 32, find them under Backlog, and **drag them onto the board** (To Do column) so they're visible to the team.
     Board link: `https://delfidiagnostics.atlassian.net/jira/software/projects/CDM/boards/32`
   - Don't claim anything is "on the board" — it isn't until the user drags it over. For draining many at once, the repo's migration script can do it in bulk: `python cdm_migration.py --project=CDM --phase=empty_backlog` (requires the repo + a valid API token).

## Subtasks (when to break one Task down)

Create `Subtask`s only to break down **one cohesive Task that closes as a unit** — the subtasks are its checklist of steps and live and die with that parent Task. Distinct, independently-closeable work is **multiple Tasks under epics**, never a pile of subtasks. If the user asks to "add a subtask to <old task>" for what is really new, independent work, gently steer them to a new Task under the right epic (this anti-pattern caused the bulk of the migration cleanup).

A subtask inherits its parent Task's epic and **cannot have its own** — so anything that needs a *different* Epic or study is a separate Task, not a subtask. When creating a subtask: set `parent` to the **Task** key (never an epic), give it its own `cat-/proj-/study-` labels, and do not set an epic. Create the parent Task first so you have its key.

## Guardrails

- Don't file new work under **Pre-2026 Legacy**.
- **Default to a single Task.** Split into multiple Tasks or a Task + Subtasks only when the work genuinely isn't one closeable unit (step 2). Don't fan a vague one-liner into many tickets — when unsure how finely to split, ask.
- **Pieces with different Epic / study / `cat-` ⇒ separate Tasks, not subtasks.** A subtask cannot carry its own epic.
- Enforce label completeness on **every** Task and Subtask: exactly one `cat-`, at least one `proj-`, exactly one `study-`. Never create anything missing one of the three.
- A Subtask's `parent` is always its Task key, never an epic; create the parent Task before its subtasks.
- Don't invent acceptance criteria or scope the user didn't imply.
- Never create without the explicit confirmation in step 7.
