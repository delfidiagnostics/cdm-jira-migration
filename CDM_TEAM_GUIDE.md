# CDM Jira — Migration Announcement & Ticket Guide

_Last updated 2026-05-31_

---

## 📋 Team announcement (copy/paste)

**CDM Jira has been reorganized — please read**

We've migrated the CDM project to a 2026-aligned structure. The cleanup ran over ~640 tickets in one pass (notifications were muted during the run, so you won't have gotten flooded with emails — but you'll notice changes).

**What changed**

- **6 epics now**, aligned to 2026 goals: *CASCADE (L201) Readout · IVD Lung PMA Submission · 4ITLR Readout · Reimbursement & Clinical Evidence · Departmental Ops · Pre-2026 Legacy*. The old epics (Corporate Goals, DELFI-L101/L201/L301) are retired.
- **Every ticket is now labeled** with a consistent 3-part scheme: one `cat-*` (kind of work), one or more `proj-*` (system/workstream), one `study-*` (study).
- **Statuses tidied up.** Stale pre-2026 work was **Dismissed** and parked under *Pre-2026 Legacy*. A new **Ongoing** status exists for recurring service work.
- **Assignees set** from our triage review — please glance at your queue and re-assign anything that looks off.

**Two asks going forward**

1. If you have **live work sitting on a pre-2026 ticket** that got Dismissed, open a **fresh ticket** for it (don't reopen the old one).
2. **How you create tickets now matters** — see the quick guide below. The big one: **new work = a new Task under an epic, not a subtask on some old task.**

Questions → [you].

---

## ✅ Quick guide — creating CDM tickets

### When you create a Task, fill these

1. **Issue type:** `Task` (use `Subtask` *only* to break down a Task you're actively working — see below).
2. **Parent / Epic:** pick exactly **one of the 6 epics** (this is how we track which goal it serves).
3. **Summary:** clear and specific.
4. **Labels (required):** exactly **one `cat-*`**, **one or more `proj-*`**, exactly **one `study-*`** (use `study-cross` if it's not study-specific).
5. **Status:** start at `To Do`.
6. **Assignee:** whoever owns it.

### 🎯 The 6 epics (pick one)

| Epic | Use for |
|---|---|
| **CASCADE (L201) Readout** | L201 readout work |
| **IVD Lung PMA Submission** | PMA package (clinical / analytical / manufacturing) |
| **4ITLR Readout** | 4ITLR readout |
| **Reimbursement & Clinical Evidence** | CU readout, CED, evidence publications |
| **Departmental Ops** | service / BAU / infra / recurring |
| **Pre-2026 Legacy** | archive only — don't file new work here |

### 🏷️ Labels at a glance

- **`cat-*`** — *kind of work*, exactly **1**: `cat-programming` · `cat-reporting` · `cat-operations`
- **`proj-*`** — *system / workstream*, **1 or more**: `cpt` · `dqr` · `edc` · `metrics` · `cancer-review` · `pipeline` · `cro` · `sample` · `data-release` · `cv` · `av` · `other` (only when nothing else fits)
- **`study-*`** — *study*, exactly **1**: `l101` · `l201` · `l301` · `4itlr` · `onom` · `cross`

How to read them together: the **Epic** says *why* (the goal), `cat-*` says *what kind* of work, `proj-*` says *where* in our stack, `study-*` says *whose data*.

### 🔄 Statuses

| Status | When |
|---|---|
| **To Do** | identified, not started |
| **In Progress** | actively being worked |
| **Done** | finished (Resolution = `Done`) |
| **Dismissed** | won't be done (Resolution = `Won't Do`) |
| **Ongoing** | recurring service work that never naturally closes (e.g. monthly refreshes, scheduled reports) |

### ⚠️ Don't use subtasks as a backlog

A **Subtask** is only the breakdown of **one Task you're doing now** — it lives and dies with that Task. **Don't pile new work as subtasks under an old long-lived task.** (That's what caused half our cleanup: subtasks can't have their own epic, so they get stuck wherever their parent is — even if the parent is closed.)

> **New piece of work → a new Task under the right epic, with its own labels.**
> Need to group related work (e.g. "all CPT work")? That's what the **`proj-*` label** is for — not a container task.
