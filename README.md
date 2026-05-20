# CDM Jira Migration

Reorganizing the **CDM (Clinical Data Management)** Jira project at Delfi Diagnostics into a 2026-aligned taxonomy: 6 epics, 3 prefixed label namespaces, 5 statuses. Currently in the **dry-run-on-TESTCDM** phase; production CDM has not been touched.

## Status (2026-05-20)

| Item | State |
|---|---|
| Taxonomy designed | ✅ |
| Triage worksheet (634 tickets) | ✅ |
| TESTCDM data reconciled to worksheet | ✅ — `--phase=verify` returns zero deltas |
| TESTCDM obsolete epics deleted | ✅ (6 epics gone; 631 tickets remain) |
| TESTCDM workflow statuses match prod CDM | ✅ `To Do / In Progress / Done / Dismissed` (renamed during testing, then reverted to mirror what CDM will look like) |
| TESTCDM `Ongoing` status added to workflow | ✅ available for future recurring-work tickets; zero migrated tickets use it |
| TESTCDM board columns | ✅ 6-column setup: `Backlog / To Do / In Progress / Done / Dismissed / Ongoing` |
| TESTCDM backlog | ✅ visible as a triage column for new tickets |
| Production CDM run | ⛔ not started |

## What's blocking us

The only remaining gap is the **production CDM run**. To proceed, the same Project Admin permission Tony was granted on TESTCDM needs to be granted on the production CDM project, and the team needs to confirm comfort with running.

The script (`cdm_migration.py --phase=all`) is the same for production. The only manual pre-flight steps on CDM are:
1. **Admin creates the 3 new epics** in CDM: `Reimbursement & Clinical Evidence`, `Departmental Ops`, `Pre-2026 Legacy`.
2. **Admin renames/consolidates** existing Era-3 epics: `CDM-676 → CASCADE (L201) Readout`; `CDM-677+678 → IVD Lung PMA Submission`; `CDM-679+680 → 4ITLR Readout`.
3. **Admin mutes the CDM notification scheme** for the run window (avoids ~480 update emails).
4. **Add `--project=CDM`** support to the script (small change — currently the project key is a constant) and update the worksheet→CDM-key resolution (identity, no mapping CSV).

## Where we're going

1. **Get admin permission** on TESTCDM + CDM (a 1-hour temporary window is sufficient).
2. **Finish TESTCDM cleanup**: run `--phase=delete_epics`, apply the two status renames, tweak the board columns in the UI.
3. **Pre-flight production CDM**: admin manually creates the 3 new epics (`Reimbursement & Clinical Evidence`, `Departmental Ops`, `Pre-2026 Legacy`) and consolidates/renames the existing Era-3 epics in CDM. Mirror of what was done in TESTCDM.
4. **Parameterize the script** to take `--project=CDM` and treat the key mapping as identity (no clone). ~30 minutes of work.
5. **Run production**: `python3 cdm_migration.py --phase=all` against CDM. Expect ~600 ops in ~1 minute.
6. **Verify**: `--phase=verify` → target zero deltas.

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

**3a. If admin permission is granted → finish TESTCDM cleanup:**
```bash
python3 cdm_migration.py --phase=delete_epics    # removes the 6 obsolete epics
```
Then rename statuses via `PUT /rest/api/3/statuses` (Done→Completed, Dismissed→Cancelled — Tony will need a short script; the API body is documented in CLAUDE.md). Then hide `Refining` and `Backlog` columns in the Jira UI (Project Settings → Board — no API for this).

**3b. If admin permission is NOT granted yet** → escalate to IT. Until then there's nothing useful to do on TESTCDM; the data is reconciled.

**4. Production CDM run (after TESTCDM cleanup is fully done):**
- Have admin pre-flight CDM in the UI: create 3 new epics (`Reimbursement & Clinical Evidence`, `Departmental Ops`, `Pre-2026 Legacy`); rename `CDM-676` to `CASCADE (L201) Readout`; consolidate `CDM-677+678` into `IVD Lung PMA Submission`; consolidate `CDM-679+680` into `4ITLR Readout`.
- Add a `--project=CDM` flag to `cdm_migration.py` so it can skip the TESTCDM mapping (small change in `phase_diff` and `phase_annotate_worksheet`).
- Have admin mute the CDM notification scheme for the run window.
- Run `python3 cdm_migration.py --project=CDM --phase=all`.
- Verify with `--phase=verify`.

## Definition of done

**TESTCDM dry-run complete** when:
- ✅ `--phase=verify` returns zero deltas (already true)
- ⏳ The 6 obsolete epics are DELETEd (blocked on admin)
- ⏳ `Done`/`Dismissed` are renamed to `Completed`/`Cancelled` (blocked on admin)
- ⏳ `Refining` and `Backlog` columns are removed from the TESTCDM board (UI-only, blocked on admin)

**Production CDM migration complete** when:
- All of the above repeated on CDM
- `--phase=verify --project=CDM` returns zero deltas
- IT has confirmed notification volume was contained
