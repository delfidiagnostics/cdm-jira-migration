# CDM Jira Migration

Reorganizing the **CDM (Clinical Data Management)** Jira project at Delfi Diagnostics into a 2026-aligned taxonomy: 6 epics, 3 prefixed label namespaces, 5 statuses. **Production migration complete (2026-05-31)** — `--project=CDM --phase=all` ran clean (0 failures, 0 residual deltas); see status below.

## Status (2026-05-31)

| Item | State |
|---|---|
| Taxonomy designed | ✅ (proj namespace grew to 12 with `proj-other` catch-all) |
| Triage worksheet (643 tickets) | ✅ (634 original + 9 added 2026-05 for CDM-698…706) |
| TESTCDM data reconciled to worksheet | ✅ — `--phase=verify` returns zero deltas |
| TESTCDM obsolete epics deleted | ✅ (6 epics gone) |
| TESTCDM workflow statuses match prod CDM | ✅ `To Do / In Progress / Done / Dismissed` (renamed during testing, then reverted to mirror what CDM will look like) |
| TESTCDM `Ongoing` status added to workflow | ✅ available for future recurring-work tickets; zero migrated tickets use it |
| TESTCDM board columns | ✅ 6-column setup: `Backlog / To Do / In Progress / Done / Dismissed / Ongoing` |
| TESTCDM backlog | ✅ visible as a triage column for new tickets |
| Subtask→Task conversion | ✅ `phase_convert_subtasks` (bulk-move API); all 6 promoted on production (CDM-644, 681, 691, 694, 695, 703) |
| Admin permission (TESTCDM + CDM) | ✅ granted on **both** projects (`ADMINISTER_PROJECTS`, `DELETE_ISSUES`, `EDIT_WORKFLOW`) |
| **Production CDM run** | ✅ **complete 2026-05-31** — 6 epics created/renamed, 634 labels, 125 transitions, 177 resolutions, 189 assignees, 320 re-parents, 6 obsolete epics deleted; `--phase=verify` = zero deltas. Pre-migration snapshot committed. |
| CDM `Ongoing` status | ✅ added to CDM workflow (transition id **52**); script's `ongoing_transition` is now project-aware |
| CDM post-migration hygiene | ✅ Labels required on Task · ⚠️ Parent not yet required (see below) |

## What's blocking us

Admin permission — the historical blocker — is **now granted on both TESTCDM and production CDM**. The remaining work before the **production CDM run** is the pre-flight + a small script parameterization, plus the team confirming comfort with running:

1. ~~**Add `--project=CDM`** support to the script~~ — ✅ done. Identity key-resolution (no mapping CSV), project-scoped state dir, and `PRESERVE_EPIC_KEYS`/`OBSOLETE_EPIC_KEYS` repointed to CDM keys all live in `EPIC_CONFIG["CDM"]`.
2. ~~**Epic create/rename/consolidate**~~ — ✅ scripted as `phase_preflight_epics` (runs first in `--phase=all`): renames `CDM-676 → CASCADE (L201) Readout`, `CDM-677 → IVD Lung PMA Submission`, `CDM-679 → 4ITLR Readout`; creates `Reimbursement & Clinical Evidence`, `Departmental Ops`, `Pre-2026 Legacy`; the empty leftovers `CDM-678`/`CDM-680` are deleted by `delete_epics`. Dry-run verified against CDM. (No longer a manual admin step.)
3. **Notification suppression is now automatic** — every PUT write passes `?notifyUsers=false` (works because admin is granted) and the bulk-move sets `sendBulkNotification:false`, so the run is silent without touching the project's notification scheme. No manual mute, no revert. This is the "rules we set" for the assignee writes that were deliberately deferred on TESTCDM.
4. **6 Subtask→Task conversions** run automatically via `phase_convert_subtasks` (CDM-644, 681, 691, 694, 695, 703) before `parents`.

Nothing left to do manually pre-run — the whole migration is in the script.

## Where we're going

1. ~~**Get admin permission** on TESTCDM + CDM~~ — ✅ done (granted on both).
2. ~~**Finish TESTCDM cleanup**~~ — ✅ done: obsolete epics deleted; statuses kept as `Done`/`Dismissed` (the team chose not to rename to `Completed`/`Cancelled`); `Ongoing` added; subtask→task conversion proven.
3. ~~**Pre-flight production CDM** (epic create/rename)~~ — ✅ scripted as `phase_preflight_epics`; dry-run verified against CDM.
4. ~~**Parameterize the script** to take `--project=CDM`~~ — ✅ done (identity key mapping, project-scoped state, CDM epic key sets in `EPIC_CONFIG`).
5. **Run production**: `python3 cdm_migration.py --project=CDM --phase=all` (notifications suppressed by the script). Expect ~600 ops + 6 epics created/renamed in ~1 minute.
6. **Verify**: `--project=CDM --phase=verify` → target zero deltas.

## Run it

```bash
# From this directory
# .env must define ATLASSIAN_EMAIL, ATLASSIAN_API_TOKEN, ATLASSIAN_BASE_URL

# read-only: snapshot + show what would change
python3 cdm_migration.py --phase=audit,diff

# dry-run a write phase
python3 cdm_migration.py --phase=assignees --dry-run

# full pipeline
python3 cdm_migration.py --phase=all

# specific cleanup (once admin permission lands)
python3 cdm_migration.py --phase=delete_epics
```

See **`CLAUDE.md`** for taxonomy, script architecture, CSV schemas, Jira API quirks, and the permanent edge-cases the script handles (Goal workflow, Subtask hierarchy, inactive users, fuzzy `/user/search`).

---

## Cold-start runbook

A future session picking this up should do exactly this:

**1. Confirm current state of TESTCDM.**
```bash
python3 cdm_migration.py --phase=audit,diff
```
Expected: zero deltas across labels/transitions/resolutions/assignees/parents.

**2. Check whether Tony now has admin permission.**
```bash
python3 -c "
import json, urllib.request
from base64 import b64encode
env = {l.split('=',1)[0]: l.split('=',1)[1].strip() for l in open('.env') if '=' in l and not l.startswith('#')}
base = env['ATLASSIAN_BASE_URL'].rstrip('/')
auth = 'Basic ' + b64encode(f\"{env['ATLASSIAN_EMAIL']}:{env['ATLASSIAN_API_TOKEN']}\".encode()).decode()
r = urllib.request.Request(f'{base}/rest/api/3/mypermissions?projectKey=TESTCDM&permissions=ADMINISTER_PROJECTS,DELETE_ISSUES,EDIT_WORKFLOW',
                           headers={'Authorization':auth,'Accept':'application/json'})
print({k: v['havePermission'] for k,v in json.loads(urllib.request.urlopen(r).read())['permissions'].items()})
"
```

**3. TESTCDM cleanup → ✅ already complete.** Admin permission is granted; the 6 obsolete epics are deleted, statuses are kept as `Done`/`Dismissed` (no rename), `Ongoing` is on the workflow, and subtask→task conversion is proven. Vestigial `Refining`/`Backlog` statuses remain (removing them needs instance-wide Jira Admin to edit the workflow graph first). `--phase=verify` returns zero deltas. Nothing left to do on TESTCDM.

**4. Production CDM run:**
- (No notification mute needed — the script passes `?notifyUsers=false` on every write and `sendBulkNotification:false` on the bulk-move, so the run is silent. The project scheme is untouched; normal user notifications continue.)
- Preview: `python3 cdm_migration.py --project=CDM --phase=preflight_epics --dry-run` (shows the 3 renames + 3 creates), then `--project=CDM --phase=audit,diff` for the full op preview (read-only).
- Run `python3 cdm_migration.py --project=CDM --phase=all`. Order: `preflight_epics` (create/rename the 6 epics) → audit/diff → labels/transitions/resolutions/assignees → `convert_subtasks` (promotes CDM-644, 681, 691, 694, 695, 703) → `parents` → deprecate/delete the obsolete epics → `empty_backlog` → `verify`.
- Verify with `--project=CDM --phase=verify`.

**5. Post-migration hygiene (CDM, UI-only):**
- **Make Labels and Parent required** on the `Task` work type. ✅ Labels is required (confirmed via createmeta). ⚠️ **Parent is not yet required** — createmeta shows only Summary/Project/Reporter/Labels. If forcing every new task under an epic is desired, set Parent → Required too.
- *(Optional)* **Add a ScriptRunner Behaviour** to enforce the `cat-*` / `proj-*` / `study-*` label structure on Create. ScriptRunner is already installed in this Atlassian instance. Without it, "Required: Labels" only enforces non-empty.
- ~~**Add `Ongoing` to the CDM workflow**~~ — ✅ done (transition id `52`). The script's `ongoing_transition` is now project-aware in `EPIC_CONFIG` (CDM=`52`, TESTCDM=`3`), so future recurring-work rows transition correctly.
- *(Optional)* **Disable the Backlog feature** if the team doesn't use it for triage. UI-only — no API toggle for `jsw.agility.backlog`.

## Definition of done

**TESTCDM dry-run complete** when:
- ✅ `--phase=verify` returns zero deltas
- ✅ The 6 obsolete epics are DELETEd
- ✅ Status names decided — kept as `Done`/`Dismissed` (the team chose *not* to rename to `Completed`/`Cancelled`; the script accepts both)
- ✅ Subtask→Task conversion proven on TESTCDM via `phase_convert_subtasks`
- ➖ Vestigial `Refining`/`Backlog` statuses remain (DELETE needs instance-wide Jira Admin to remove them from the workflow first; kept as-is — see CLAUDE.md)

**Production CDM migration complete** — ✅ **done 2026-05-31**:
- ✅ All of the above applied to CDM (6 epics, 634 labels, 125 transitions, 177 resolutions, 189 assignees, 320 re-parents, 6 conversions, 6 obsolete epics deleted)
- ✅ `--phase=verify --project=CDM` returns zero deltas
- ✅ Notifications suppressed (`?notifyUsers=false` on every write)
- ✅ `Ongoing` added to CDM workflow; Labels required on Task (⚠️ Parent still optional)
- ✅ Pre-migration snapshot committed (`CDM_PREMIGRATION_SNAPSHOT_2026-05-31T142034.{json,csv}`)
