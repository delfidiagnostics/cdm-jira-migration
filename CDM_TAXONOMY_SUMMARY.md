# CDM Project Taxonomy — Summary

Every ticket answers four questions:

1. **Which 2026 corporate goal does this serve?** → its **Epic**
2. **What kind of work is it?** → `cat-*` label (one of 3)
3. **Which project / system is it on?** → `proj-*` label
4. **Which study, if any?** → `study-*` label

Hierarchy below the epic is **Task → Subtask** only (Jira's limit). The "*software → listing → work*" feel is captured by **`proj-*` label → Task (the listing/feature) → Subtasks (the work)**.

---

## 1. Hierarchy

```
Epic               = a 2026 corporate goal — or Departmental Ops — or Pre-2026 Legacy
 └─ Task           = a deliverable: a feature, a DQR listing, a refactor, a report, a service activity
     └─ Subtask    = a unit of work to deliver it
```

Labels apply on **both** Task and Subtask so JQL filters by label work either way.

---

## 2. Epics — 6 total (4 corporate goal-aligned + 2 special)

| # | Epic | Maps to |
|---|---|---|
| 1 | **CASCADE (L201) Readout** | *Successful readout of CASCADE (L201) primary performance using locked IVD test by 2H 2026* |
| 2 | **IVD Lung PMA Submission** | *Complete submission-ready PMA package for IVD Lung test (clinical, analytical, manufacturing)* |
| 3 | **4ITLR Readout** | *Successful readout of 4ITLR by July, present data at a conference in 2026* |
| 4 | **Reimbursement & Clinical Evidence** | *Clinical Utility readout by January; CED Highmark submission Q4; two major clinical evidence publications Q2* |
| 5 | **Departmental Ops** | Service work, BAU, infra, recurring metrics — anything that doesn't tie to a 2026 corporate goal |
| 6 | **Pre-2026 Legacy** | Pure archive — all pre-2026 tickets land here, none of it is live work |

**Why this shape**: only one epic slot per ticket, so we use it for the single most important question — "which corporate goal does this serve?" Departmental Ops captures BAU; Legacy keeps the historical pile out of the way without losing it.

---

## 3. Labels — three small prefixed namespaces

Prefixes (`cat-`, `proj-`, `study-`) make JQL queries unambiguous and bulk operations safe. Three namespaces, kept small to minimize ambiguity at filing time.

### 3a. `cat-*` (kind of work) — exactly 1 per issue

| Label | Filing rule (close-the-ticket test) |
|---|---|
| `cat-programming` | Closes when code is merged — scripts, pipelines, automation, repo work, notebooks |
| `cat-reporting` | Closes when a report / listing / dashboard is delivered or refreshed — anything humans consume |
| `cat-operations` | Everything else — EDC config, UAT, validation, SOPs, training, planning, vendor mgmt, audit, threshold-release activities |

**Why three**: programming and reporting are the two clearest, highest-volume buckets. Everything else fits under operations. The "close-the-ticket test" eliminates filing ambiguity.

### 3b. `proj-*` (project / system / product) — 1+ per issue

| Label | Covers |
|---|---|
| `proj-cpt` | Clean Patient Tracker (any study) — multi-listing artifact |
| `proj-dqr` | Standalone DQR listings *not inside CPT* — and the DQR platform itself |
| `proj-edc` | EDC builds, releases, edit checks, page configuration, edit-check logic |
| `proj-metrics` | Study dashboards, KPI reports, EDC page metrics, monthly metric reports, case-tracker dashboards (Clario, etc.) |
| `proj-cancer-review` | Cancer adjudication and review reporting — any study (CDR is the L201 instance) |
| `proj-pipeline` | Data / processing pipelines — SURF read-ins, ADaM derivations, analysis stitchers |
| `proj-cro` | CRO interfacing and external reporting — any CRO (PPD is the L201 CRO) |
| `proj-sample` | Sample tracking, sample-recollection, sample-processing operations |
| `proj-data-release` | Clinical data threshold/snapshot release governance — CDCP drafting, cleaning plans, snapshot sign-offs, release approval forms, S3 folder setup tied to a formal data release event |
| `proj-cv` | Clinical Validation workstream — CV data sets, CV readout artifacts |
| `proj-av` | Analytical Validation workstream — AV data sets, AV readouts |
| `proj-other` | Catch-all for operational/admin/training work with no natural data-workstream fit — e.g. SOP authoring, hiring, training (Intro to R), team-process tasks. Keeps the "1+ proj per issue" rule satisfiable for ops items. |

**Why 12 labels**: the first 11 are grounded in actual CDM data workstreams and are cross-study by design (vendor / CRO / study-specific names like Clario, CDR, PPD live in the summary, not the label). `proj-other` was added as the catch-all so operational/admin tasks still carry a `proj-*` label. Stack labels freely when a ticket spans systems (e.g. `proj-cpt` + `proj-data-release` for threshold-release cleaning of CPT data). Case-tracker work (Clario, Suspicious Nodule, etc.) lives under `proj-metrics` since the deliverable is a tracker/dashboard.

### 3c. `study-*` (study identifier) — exactly 1 per issue

| Label | Covers |
|---|---|
| `study-l101` | DELFI-L101 |
| `study-l201` | DELFI-L201 (CASCADE) |
| `study-l301` | DELFI-L301 |
| `study-4itlr` | 4ITLR |
| `study-onom` | Onom |
| `study-cross` | Infrastructure, tooling, or work that intentionally spans multiple studies |

Every ticket gets exactly one `study-*` label (use `study-cross` when no single study applies). Forcing a choice removes "did I forget to label this?" ambiguity.

---

## 4. Statuses (5)

| Status | When to use |
|---|---|
| **To Do** | Identified, not started |
| **In Progress** | Actively being worked |
| **Done** | Closed successfully (Resolution = `Done`) |
| **Ongoing** | Recurring service work — monthly CDR refreshes, scheduled metric reports, repeating data snapshots. Never reaches Done because it recurs. |
| **Dismissed** | Won't be done (Resolution = `Won't Do`) |

**Why these five**: covers the work-in-flight lifecycle plus an "Ongoing" lane for recurring service work that never naturally closes, and "Dismissed" distinguished from "Done" via the Resolution field so they don't blur together in reporting.

**Implementation note**: TESTCDM has all five statuses in its workflow. Production CDM still has only four (`To Do / In Progress / Done / Dismissed`) — the `Ongoing` status will need to be added to CDM's workflow once we're ready to use it for recurring tickets. Until then, the migration script collapses `Ongoing → In Progress`. Zero worksheet rows currently use `Ongoing`, so this is dormant.

---

## 5. Migration rules at a glance

> **Implementation status (2026-05-31): complete.** All rules below were applied to **TESTCDM** (the dry-run clone) and then to **production CDM** — `cdm_migration.py --project=CDM --phase=all` ran clean (0 failures) and `--phase=verify` returns zero deltas. The full pipeline is scripted end-to-end, including epic create/rename (`phase_preflight_epics`) and Subtask→Task promotion (`phase_convert_subtasks`); notifications were suppressed via `?notifyUsers=false`. `Ongoing` is now in the CDM workflow. See `README.md` for status and `CLAUDE.md` for phase/architecture detail.

**Pre-flight:**

0. **Snapshot the project to CSV** via the Jira UI before any bulk operation. Five-minute insurance against a bad write.
0a. **Communicate to the team** that pre-2026 tickets will be Dismissed. Anyone with active work on a pre-2026 ticket should open a fresh 2026 ticket *before* the dismiss pass — otherwise their work disappears from boards.

**Structural changes:**

*Both steps below are now scripted in `phase_preflight_epics` (idempotent, runs first in `--phase=all`); the Era-3 epics CDM-676..680 are empty shells, so consolidation is just rename-the-survivor + delete-the-empties.*

1. **Create**: Departmental Ops epic, Pre-2026 Legacy epic, Reimbursement & Clinical Evidence epic.
2. **Rename / consolidate**:
   - [CDM-676](https://delfidiagnostics.atlassian.net/browse/CDM-676) → "**CASCADE (L201) Readout**"
   - [CDM-677](https://delfidiagnostics.atlassian.net/browse/CDM-677) → "**IVD Lung PMA Submission**"; [CDM-678](https://delfidiagnostics.atlassian.net/browse/CDM-678) (empty) deleted by `delete_epics`
   - [CDM-679](https://delfidiagnostics.atlassian.net/browse/CDM-679) → "**4ITLR Readout**"; [CDM-680](https://delfidiagnostics.atlassian.net/browse/CDM-680) (empty) deleted by `delete_epics`

**Cleanup pass:**

3. **Dismiss any ticket created before 2026-01-01 that is not Done.** This sweeps out 4 years of accumulated open-but-stale work in one pass (~135 tickets). If a pre-2026 ticket still represents real work, open a fresh 2026 ticket for it — don't resurrect the old one.
4. **Set the Resolution field on close, and backfill existing Dismissed tickets.** The current workflow auto-sets Resolution = `Done` on every closing transition, *including* Dismissed — so today all 49 existing Dismissed tickets read Resolution = `Done`, indistinguishable from real wins. Migration steps:
   - Dismissed tickets (the ~135 pre-2026 ones in rule 3) → set Resolution = `Won't Do` (or `Out of Scope`)
   - Done tickets → Resolution = `Done` (already correct in most cases)
   - **Workflow fix (admin task)**: configure the Dismissed transition's post-function to set Resolution = `Won't Do` automatically so this doesn't drift again.
5. **Cascade Dismiss to orphaned Subtasks.** The 6 stale 2022 Goals under [CDM-46](https://delfidiagnostics.atlassian.net/browse/CDM-46) get Dismissed by rule 3, but their 21 child Subtasks need to be Dismissed too with the same Resolution. The connector doesn't cascade automatically — explicit step.
6. **Dismiss** the 3 dismissed UAT Tests ([CDM-140](https://delfidiagnostics.atlassian.net/browse/CDM-140), [141](https://delfidiagnostics.atlassian.net/browse/CDM-141), [142](https://delfidiagnostics.atlassian.net/browse/CDM-142)) and the obvious test data ([CDM-160](https://delfidiagnostics.atlassian.net/browse/CDM-160), [CDM-157](https://delfidiagnostics.atlassian.net/browse/CDM-157)). *(Connector can Dismiss; deletion needs UI.)*

**Re-organization pass:**

7. **Re-parent every remaining open ticket** (2026-created or still-actively-worked) to one of the 6 epics, per the triage worksheet with your review.
8. **Move all pre-2026 tickets** (now either Done or freshly Dismissed per rule 3) under **Pre-2026 Legacy** for tidy archival. Legacy becomes a pure archive — no live work.
9. **Close out** [CDM-46](https://delfidiagnostics.atlassian.net/browse/CDM-46), [CDM-79](https://delfidiagnostics.atlassian.net/browse/CDM-79), [CDM-80](https://delfidiagnostics.atlassian.net/browse/CDM-80), [CDM-467](https://delfidiagnostics.atlassian.net/browse/CDM-467) once empty.

**Labels, assignees, and hygiene:**

10. **Apply `cat-*` + `proj-*` + `study-*` labels** to every open 2026 ticket. Optional: backfill Done tickets for searchability.
11. **Strip legacy labels** (`DQR_Listings`, `CDM-Tools`, `Departmental`, `Corporate`, `IVD_L201_Validation`, `L201`, `UAT`, `4ITLR_Dataset`, `Study`, `CPT_Component`, `Ad_Hoc_Report`, `Functional-Area`, `threshold_release`, `Translational_Research`) once the new labels are verified — same pass as rule 10. Otherwise every ticket carries both old and new conventions and queries get noisy.
12. **Assign unassigned tickets to the reporter.** Edge cases: if the reporter is no longer active or is a system user, leave assignee null.
