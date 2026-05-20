#!/usr/bin/env python3
"""
TESTCDM dry-run migration — combined script.

Phases (run any subset via --phase=a,b,c or --phase=all):
  audit              Paginated /search/jql; snapshot to .testcdm-current/audit.json
  diff               Build remaining_ops.json from audit + worksheet
  annotate_worksheet Append [subtask: ...] tag to Subtask rows' notes column
  labels             PUT labels (cat-* + proj-* + study-*)
  transitions        POST transitions (Goal fallback 51 -> 31)
  resolutions        PUT resolution overrides
  assignees          PUT assignees (name -> accountId lookup; skips inactive)
  parents            PUT parents (skips Subtasks; they inherit via Goal/Task)
  deprecate_epics    Prepend `(DEPRECATED)` to obsolete epic summaries
  delete_epics       DELETE the 6 obsolete epics (verifies 0 children first)
  empty_backlog      Promote every backlog item to the board (Team-Managed
                     projects demote re-parented items to backlog by default)
  verify             Re-audit + re-diff; should print zero deltas
  all                Run everything in order

Reads .env for ATLASSIAN_EMAIL / ATLASSIAN_API_TOKEN / ATLASSIAN_BASE_URL.
Continues on per-ticket failures; reports at end. Logs to cdm_migration_log.json.
"""

import argparse
import csv
import json
import sys
import time
from base64 import b64encode
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from urllib import request as urlreq, error as urlerr
from urllib.parse import urlencode

ROOT = Path(__file__).parent
STATE_DIR = ROOT / ".testcdm-current"
WORKSHEET = ROOT / "CDM_TRIAGE_WORKSHEET.csv"
MAPPING = ROOT / "CDM_TESTCDM_KEY_MAPPING.csv"
REMAINING_OPS = ROOT / "remaining_ops.json"
LOG_PATH = ROOT / "cdm_migration_log.json"

PROJECT = "TESTCDM"

PRESERVE_EPIC_KEYS = {
    "TESTCDM-630",  # CASCADE (L201) Readout
    "TESTCDM-631",  # IVD Lung PMA Submission
    "TESTCDM-633",  # 4ITLR Readout
    "TESTCDM-635",  # Reimbursement & Clinical Evidence
    "TESTCDM-636",  # Departmental Ops
    "TESTCDM-637",  # Pre-2026 Legacy
}

OBSOLETE_EPIC_KEYS = [
    "TESTCDM-1",    # Corporate Goals (was CDM-46)
    "TESTCDM-29",   # DELFI-L101    (was CDM-79)
    "TESTCDM-282",  # DELFI-L201    (was CDM-80)
    "TESTCDM-521",  # DELFI-L301    (was CDM-467)
    "TESTCDM-632",  # [CONSOLIDATED] AV/CV -> PMA
    "TESTCDM-634",  # [CONSOLIDATED] 4ITLR dataset
]

# Worksheet proposed_status -> (acceptable workflow status names, transition id).
# Status names: accepts both pre-rename (CDM: Done/Dismissed) and post-rename
# (TESTCDM at various points: Completed/Cancelled) — first name is canonical.
# Transition IDs: 31/51/11 are stable. In Progress is 21 on CDM but became 2 on
# TESTCDM after the workflow edit that added Ongoing — neither is hit on the
# current run because no row needs an In Progress transition, but keep this in
# mind if you ever add an "In Progress" row to the worksheet.
# Ongoing status (id 11266) is project-scoped to TESTCDM. On CDM this transition
# won't exist until the workflow is edited similarly; zero worksheet rows use
# Ongoing anyway, so the mapping is dormant in practice.
STATUS_TO_TRANSITION = {
    "Completed": (("Completed", "Done"), "31"),
    "Cancelled": (("Cancelled", "Dismissed"), "51"),
    "In Progress": (("In Progress",), "21"),
    "Ongoing": (("Ongoing", "In Progress"), "3"),
    "To Do": (("To Do",), None),
}

RESOLUTION_BY_PROPOSED_STATUS = {
    "Completed": "Done",
    "Cancelled": "Won't Do",
}

SUBTASK_TYPES = {"sub-task", "subtask"}

# Former employees — Jira blocks new assignments to inactive accounts (even though
# their existing assignments persist). Skip assignment ops targeting these names.
INACTIVE_ASSIGNEES = {
    "Erica Peters",
    "Lee Ming Sun",
    "Vincent Puga-Aragon",
    "Clarice Grant",
    "Pavlova Nalley",
    "Spencer King",
}


# ---------- env + http ----------
def load_env():
    env = {}
    with open(ROOT / ".env") as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                env[k] = v
    return env


def make_call(base, auth_header):
    headers = {
        "Authorization": auth_header,
        "Accept": "application/json",
        "Content-Type": "application/json",
    }

    def call(method, path, body=None, timeout=30, max_retries=5):
        url = f"{base}{path}"
        data = json.dumps(body).encode() if body is not None else None
        backoff = 1.0
        for attempt in range(max_retries + 1):
            req = urlreq.Request(url, data=data, headers=headers, method=method)
            try:
                with urlreq.urlopen(req, timeout=timeout) as resp:
                    return resp.status, resp.read().decode()
            except urlerr.HTTPError as e:
                if e.code == 429 and attempt < max_retries:
                    time.sleep(backoff)
                    backoff = min(backoff * 2, 30)
                    continue
                return e.code, e.read().decode()
            except Exception as e:
                if attempt < max_retries:
                    time.sleep(backoff)
                    backoff = min(backoff * 2, 30)
                    continue
                return 0, f"EXCEPTION: {e}"
        return 0, "EXCEPTION: exhausted retries"

    return call


# ---------- audit ----------
def phase_audit(call):
    print("\n=== AUDIT ===")
    STATE_DIR.mkdir(exist_ok=True)
    fields = "summary,issuetype,status,resolution,parent,labels,assignee"
    jql = f"project = {PROJECT} ORDER BY key ASC"
    audit = {}
    next_token = None
    pages = 0
    t0 = time.time()
    while True:
        params = {"jql": jql, "fields": fields, "maxResults": 100}
        if next_token:
            params["nextPageToken"] = next_token
        path = "/rest/api/3/search/jql?" + urlencode(params)
        status, body = call("GET", path)
        if status != 200:
            print(f"AUDIT FAILED: {status} {body[:300]}")
            sys.exit(1)
        data = json.loads(body)
        issues = data.get("issues", [])
        for issue in issues:
            k = issue["key"]
            f = issue.get("fields", {})
            audit[k] = {
                "key": k,
                "summary": f.get("summary"),
                "issuetype": (f.get("issuetype") or {}).get("name"),
                "status": (f.get("status") or {}).get("name"),
                "resolution": (f.get("resolution") or {}).get("name") if f.get("resolution") else None,
                "parent": (f.get("parent") or {}).get("key") if f.get("parent") else None,
                "labels": f.get("labels") or [],
                "assignee_name": (f.get("assignee") or {}).get("displayName") if f.get("assignee") else None,
                "assignee_account": (f.get("assignee") or {}).get("accountId") if f.get("assignee") else None,
            }
        pages += 1
        next_token = data.get("nextPageToken")
        is_last = data.get("isLast", True)
        print(f"  page {pages}: +{len(issues)} (total {len(audit)})")
        if is_last or not next_token:
            break
    print(f"Audited {len(audit)} TESTCDM issues in {time.time()-t0:.1f}s")
    with open(STATE_DIR / "audit.json", "w") as f:
        json.dump(audit, f, indent=2)
    return audit


def load_audit():
    p = STATE_DIR / "audit.json"
    if not p.exists():
        print("No audit.json found. Run --phase=audit first.")
        sys.exit(1)
    with open(p) as f:
        return json.load(f)


# ---------- diff ----------
def load_worksheet():
    with open(WORKSHEET) as f:
        return list(csv.DictReader(f))


def load_mapping():
    cdm_to_test = {}
    with open(MAPPING) as f:
        for r in csv.DictReader(f):
            if r["cdm_key"]:
                cdm_to_test[r["cdm_key"]] = r["testcdm_key"]
    return cdm_to_test


def build_epic_name_to_key(audit):
    expected_names = {
        "CASCADE (L201) Readout",
        "IVD Lung PMA Submission",
        "4ITLR Readout",
        "Reimbursement & Clinical Evidence",
        "Departmental Ops",
        "Pre-2026 Legacy",
    }
    name_to_key = {}
    for k, v in audit.items():
        if v.get("issuetype") == "Epic" and v.get("summary") in expected_names:
            name_to_key[v["summary"]] = k
    missing = expected_names - set(name_to_key.keys())
    if missing:
        print(f"WARNING: missing new-taxonomy epics by name: {missing}")
    return name_to_key


def phase_diff():
    print("\n=== DIFF ===")
    audit = load_audit()
    worksheet = load_worksheet()
    cdm_to_test = load_mapping()
    epic_name_to_key = build_epic_name_to_key(audit)
    print(f"Epic name -> key:")
    for n, k in epic_name_to_key.items():
        print(f"  {k}  {n}")

    skip_keys = PRESERVE_EPIC_KEYS | set(OBSOLETE_EPIC_KEYS)

    label_ops = []
    transition_ops = []
    resolution_ops = []
    assignee_ops = []
    parent_ops = []
    distinct_assignees = set()
    skipped_subtasks_parent = 0

    for row in worksheet:
        cdm_key = row["key"]
        tk = cdm_to_test.get(cdm_key)
        if not tk or tk in skip_keys:
            continue
        cur = audit.get(tk)
        if not cur:
            print(f"  WARN: {tk} ({cdm_key}) not in audit")
            continue

        itype = (cur.get("issuetype") or "").lower()
        is_subtask = itype in SUBTASK_TYPES
        is_goal = cur.get("issuetype") == "Goal"

        # labels
        target_labels = []
        if row["cat-*"]:
            target_labels.append(row["cat-*"])
        if row["proj-*"]:
            target_labels.extend(p for p in row["proj-*"].split(",") if p)
        if row["study-*"]:
            target_labels.append(row["study-*"])
        if sorted(target_labels) != sorted(cur.get("labels") or []):
            label_ops.append({"key": tk, "target_labels": target_labels,
                              "current_labels": sorted(cur.get("labels") or [])})

        # transition + resolution
        proposed_status = row["proposed_status"]
        target_names, tid = STATUS_TO_TRANSITION.get(proposed_status, (("To Do",), None))
        target_resolution = RESOLUTION_BY_PROPOSED_STATUS.get(proposed_status)

        # Goal workflow has no Cancelled/Dismissed transition; fallback lands at
        # Completed+Won't Do (or pre-rename Done+Won't Do, for CDM). Treat both as satisfied.
        goal_cancelled_satisfied = (
            is_goal
            and proposed_status == "Cancelled"
            and cur.get("status") in ("Completed", "Done")
            and cur.get("resolution") == "Won't Do"
        )

        if not goal_cancelled_satisfied and tid and cur.get("status") not in target_names:
            transition_ops.append({"key": tk, "transition_id": tid,
                                   "target_status": target_names[0],
                                   "current_status": cur.get("status"),
                                   "issuetype": cur.get("issuetype")})

        if not goal_cancelled_satisfied and target_resolution \
                and cur.get("resolution") != target_resolution:
            resolution_ops.append({"key": tk, "target_resolution": target_resolution,
                                   "current_resolution": cur.get("resolution")})

        # assignees: strip "(← reporter)" annotation; skip placeholders and inactive
        proposed_assignee = (row.get("proposed_assignee") or "").strip()
        # Worksheet uses " (← reporter)" to flag reporter-fallbacks. Strip for lookup.
        clean_assignee = proposed_assignee.replace(" (← reporter)", "").strip()
        skip_assignee = (
            not clean_assignee
            or clean_assignee.upper() == "UNASSIGNED"
            or clean_assignee.endswith("(inactive)")
            or clean_assignee in INACTIVE_ASSIGNEES
        )
        if not skip_assignee:
            distinct_assignees.add(clean_assignee)
            if cur.get("assignee_name") != clean_assignee:
                assignee_ops.append({"key": tk, "target_name": clean_assignee,
                                     "current_name": cur.get("assignee_name")})

        # parents (skip subtasks; they inherit via Task/Goal parent)
        proposed_epic_name = (row.get("proposed_epic") or "").strip()
        if proposed_epic_name and proposed_epic_name in epic_name_to_key:
            target_parent = epic_name_to_key[proposed_epic_name]
            if is_subtask:
                skipped_subtasks_parent += 1
            elif cur.get("parent") != target_parent:
                parent_ops.append({"key": tk, "target_parent": target_parent,
                                   "current_parent": cur.get("parent"),
                                   "issuetype": cur.get("issuetype"),
                                   "proposed_epic_name": proposed_epic_name})

    summary = {
        "labels": len(label_ops),
        "transitions": len(transition_ops),
        "resolutions": len(resolution_ops),
        "assignees": len(assignee_ops),
        "parents": len(parent_ops),
        "distinct_assignee_names": len(distinct_assignees),
        "subtasks_with_parent_op_skipped": skipped_subtasks_parent,
    }
    print(f"\nDiff summary: {json.dumps(summary, indent=2)}")

    out = {
        "epic_name_to_key": epic_name_to_key,
        "label_ops": label_ops,
        "transition_ops": transition_ops,
        "resolution_ops": resolution_ops,
        "assignee_ops": assignee_ops,
        "parent_ops": parent_ops,
        "distinct_assignees": sorted(distinct_assignees),
    }
    with open(REMAINING_OPS, "w") as f:
        json.dump(out, f, indent=2)
    print(f"Wrote {REMAINING_OPS}")
    return out


def load_diff():
    if not REMAINING_OPS.exists():
        print(f"No {REMAINING_OPS}. Run --phase=diff first.")
        sys.exit(1)
    with open(REMAINING_OPS) as f:
        return json.load(f)


# ---------- worksheet annotation ----------
def phase_annotate_worksheet():
    print("\n=== ANNOTATE WORKSHEET (Subtasks + inactive assignees) ===")
    audit = load_audit()
    cdm_to_test = load_mapping()
    n_subtask = 0
    n_inactive = 0
    rows = []
    with open(WORKSHEET) as f:
        reader = csv.DictReader(f)
        fieldnames = reader.fieldnames
        for r in reader:
            tk = cdm_to_test.get(r["key"])
            if tk and (audit.get(tk, {}).get("issuetype") or "").lower() in SUBTASK_TYPES:
                tag = f"[subtask: parent stays at {r.get('parent_key','')}; epic inherited]"
                notes = (r.get("notes") or "").strip()
                if tag not in notes:
                    r["notes"] = f"{notes}; {tag}".lstrip("; ")
                    n_subtask += 1
            # Tag former-employee proposed assignees
            pa = (r.get("proposed_assignee") or "").strip()
            clean = pa.replace(" (← reporter)", "").strip()
            if clean in INACTIVE_ASSIGNEES and not pa.endswith("(inactive)"):
                r["proposed_assignee"] = f"{pa} (inactive)" if pa else ""
                n_inactive += 1
            rows.append(r)
    with open(WORKSHEET, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames)
        w.writeheader()
        w.writerows(rows)
    print(f"Tagged {n_subtask} subtask rows; flagged {n_inactive} inactive-assignee rows")


# ---------- parallel runner ----------
def run_parallel(ops, fn, label, workers=10):
    if not ops:
        print(f"  {label}: nothing to do")
        return []
    t0 = time.time()
    results = []
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = [pool.submit(fn, op) for op in ops]
        for i, fut in enumerate(as_completed(futures), 1):
            results.append(fut.result())
            if i % 50 == 0 or i == len(ops):
                print(f"  {label}: {i}/{len(ops)} ({time.time()-t0:.1f}s)")
    ok = sum(1 for r in results if r["result"] == "ok")
    other = [r for r in results if r["result"] != "ok"]
    print(f"  {label}: {ok} ok, {len(other)} other ({len(results)} total)")
    for r in other[:10]:
        msg = r.get("error") or r["result"]
        print(f"    {r['key']:14s} {str(msg)[:140]}")
    return results


# ---------- phases that write ----------
def phase_labels(call, dry_run=False):
    print("\n=== LABELS ===")
    ops = load_diff()["label_ops"]
    if dry_run:
        print(f"  [dry-run] {len(ops)} label updates")
        return []
    def apply(op):
        s, b = call("PUT", f"/rest/api/3/issue/{op['key']}",
                    body={"fields": {"labels": op["target_labels"]}})
        if 200 <= s < 300:
            return {"key": op["key"], "phase": "labels", "result": "ok"}
        return {"key": op["key"], "phase": "labels", "result": "failed",
                "error": f"{s}: {b[:200]}"}
    return run_parallel(ops, apply, "labels")


def phase_transitions(call, dry_run=False):
    print("\n=== TRANSITIONS ===")
    ops = load_diff()["transition_ops"]
    if dry_run:
        print(f"  [dry-run] {len(ops)} transitions")
        return []
    def apply(op):
        k, tid = op["key"], op["transition_id"]
        s, b = call("POST", f"/rest/api/3/issue/{k}/transitions?notifyUsers=false",
                    body={"transition": {"id": tid}})
        if 200 <= s < 300:
            return {"key": k, "phase": "transitions", "result": "ok"}
        if "is not valid for this issue" in b and tid == "51":
            s2, b2 = call("POST", f"/rest/api/3/issue/{k}/transitions?notifyUsers=false",
                          body={"transition": {"id": "31"}})
            if 200 <= s2 < 300:
                return {"key": k, "phase": "transitions", "result": "fallback_done"}
            return {"key": k, "phase": "transitions", "result": "failed",
                    "error": f"primary {s}; fallback {s2}: {b2[:200]}"}
        return {"key": k, "phase": "transitions", "result": "failed",
                "error": f"{s}: {b[:200]}"}
    return run_parallel(ops, apply, "transitions")


def phase_resolutions(call, dry_run=False):
    print("\n=== RESOLUTIONS ===")
    ops = load_diff()["resolution_ops"]
    if dry_run:
        print(f"  [dry-run] {len(ops)} resolution overrides")
        return []
    def apply(op):
        s, b = call("PUT", f"/rest/api/3/issue/{op['key']}",
                    body={"fields": {"resolution": {"name": op["target_resolution"]}}})
        if 200 <= s < 300:
            return {"key": op["key"], "phase": "resolutions", "result": "ok"}
        return {"key": op["key"], "phase": "resolutions", "result": "failed",
                "error": f"{s}: {b[:200]}"}
    return run_parallel(ops, apply, "resolutions")


def phase_assignees(call, dry_run=False):
    print("\n=== ASSIGNEES ===")
    diff = load_diff()
    ops = diff["assignee_ops"]
    distinct = diff["distinct_assignees"]
    print(f"  resolving {len(distinct)} distinct names -> accountId")
    name_to_account = {}
    unresolved = []
    for name in distinct:
        path = "/rest/api/3/user/search?" + urlencode({"query": name})
        s, b = call("GET", path)
        match = None
        if s == 200:
            users = json.loads(b)
            # Require EXACT displayName match. Fuzzy fallback is unsafe — partial
            # matches mis-resolve (e.g. "Lee Ming Sun" -> "Lee-Ann Smith-Freeman").
            match = next((u for u in users if u.get("displayName") == name
                          and u.get("active") is not False), None)
        if match and match.get("accountId"):
            name_to_account[name] = match["accountId"]
        else:
            unresolved.append(name)

    # Fallback for users not exact-matched in search: lift accountId from CDM source.
    # Track inactive accounts — Jira blocks new assignments to inactive users.
    inactive_names = set()
    if unresolved:
        print(f"  {len(unresolved)} unresolved via /user/search; trying CDM source-ticket fallback")
        worksheet = load_worksheet()
        for name in list(unresolved):
            src_row = next(
                (r for r in worksheet
                 if (r.get("proposed_assignee") or "").replace(" (← reporter)", "").strip() == name
                 and r.get("current_assignee") == name),
                None,
            )
            if not src_row:
                continue
            s, b = call("GET", f"/rest/api/3/issue/{src_row['key']}?fields=assignee")
            if s == 200:
                a = (json.loads(b).get("fields", {}) or {}).get("assignee") or {}
                if a.get("accountId"):
                    is_active = a.get("active", True)
                    if is_active:
                        name_to_account[name] = a["accountId"]
                    else:
                        inactive_names.add(name)
                    unresolved.remove(name)
                    print(f"    fallback {name!r} -> {a['accountId']} (active={is_active})")
    print(f"  total resolved {len(name_to_account)}/{len(distinct)}; "
          f"inactive (skipped) {len(inactive_names)}; unresolved {len(unresolved)}")
    if inactive_names:
        print(f"  inactive: {sorted(inactive_names)}")
    if unresolved:
        print(f"  unresolved: {unresolved}")
    if dry_run:
        print(f"  [dry-run] {len(ops)} assignee updates")
        return []
    def apply(op):
        if op["target_name"] in inactive_names:
            return {"key": op["key"], "phase": "assignees", "result": "skipped_inactive",
                    "target_name": op["target_name"]}
        aid = name_to_account.get(op["target_name"])
        if not aid:
            return {"key": op["key"], "phase": "assignees", "result": "skipped_no_account",
                    "target_name": op["target_name"]}
        s, b = call("PUT", f"/rest/api/3/issue/{op['key']}",
                    body={"fields": {"assignee": {"accountId": aid}}})
        if 200 <= s < 300:
            return {"key": op["key"], "phase": "assignees", "result": "ok"}
        return {"key": op["key"], "phase": "assignees", "result": "failed",
                "error": f"{s}: {b[:200]}"}
    return run_parallel(ops, apply, "assignees")


def phase_parents(call, dry_run=False):
    print("\n=== PARENTS ===")
    ops = load_diff()["parent_ops"]
    if dry_run:
        print(f"  [dry-run] {len(ops)} parent re-parents")
        return []
    def apply(op):
        s, b = call("PUT", f"/rest/api/3/issue/{op['key']}",
                    body={"fields": {"parent": {"key": op["target_parent"]}}})
        if 200 <= s < 300:
            return {"key": op["key"], "phase": "parents", "result": "ok"}
        return {"key": op["key"], "phase": "parents", "result": "failed",
                "error": f"{s}: {b[:200]}",
                "issuetype": op.get("issuetype")}
    return run_parallel(ops, apply, "parents")


def phase_empty_backlog(call, dry_run=False):
    """Promote every backlog item to the board. In Team-Managed Software projects,
    re-parented and newly-transitioned items get demoted to backlog by default;
    this phase clears them out so they render on the Board view."""
    print("\n=== EMPTY BACKLOG ===")
    # Discover board ID for the project
    s, b = call("GET", "/rest/agile/1.0/board?projectKeyOrId=" + PROJECT)
    if s != 200:
        print(f"  board lookup failed: {s} {b[:200]}")
        return [{"key": "-", "phase": "empty_backlog", "result": "failed",
                 "error": f"board lookup {s}"}]
    boards = json.loads(b).get("values", [])
    if not boards:
        print(f"  no board found for {PROJECT}")
        return []
    board_id = boards[0]["id"]
    # Page through backlog
    keys = []
    start = 0
    while True:
        s, b = call("GET", f"/rest/agile/1.0/board/{board_id}/backlog?startAt={start}&maxResults=100&fields=summary")
        if s != 200:
            break
        d = json.loads(b)
        page = [i["key"] for i in d.get("issues", [])]
        keys.extend(page)
        if not page or d.get("isLast") or start + len(page) >= d.get("total", 0):
            break
        start += len(page)
    if not keys:
        print(f"  backlog already empty for board {board_id}")
        return []
    if dry_run:
        print(f"  [dry-run] would move {len(keys)} items to board {board_id}")
        return []
    # Batch the POST (Atlassian caps at 50 per call)
    results = []
    for i in range(0, len(keys), 50):
        batch = keys[i:i+50]
        s, b = call("POST", f"/rest/agile/1.0/board/{board_id}/issue", body={"issues": batch})
        if 200 <= s < 300:
            results.append({"key": f"batch_{i}", "phase": "empty_backlog",
                            "result": "ok", "count": len(batch)})
        else:
            results.append({"key": f"batch_{i}", "phase": "empty_backlog",
                            "result": "failed", "error": f"{s}: {b[:200]}"})
    moved = sum(r.get("count", 0) for r in results if r["result"] == "ok")
    print(f"  moved {moved}/{len(keys)} items from backlog to board {board_id}")
    return results


def phase_deprecate_epics(call, dry_run=False):
    """Prepend '(DEPRECATED) ' to each obsolete epic's summary. Idempotent."""
    print("\n=== DEPRECATE OBSOLETE EPICS (summary prefix) ===")
    results = []
    for ek in OBSOLETE_EPIC_KEYS:
        s, b = call("GET", f"/rest/api/3/issue/{ek}?fields=summary")
        if s == 404:
            print(f"  {ek}: already deleted")
            results.append({"key": ek, "phase": "deprecate_epics", "result": "ok"})
            continue
        if s != 200:
            print(f"  {ek}: fetch failed {s}")
            results.append({"key": ek, "phase": "deprecate_epics",
                            "result": "failed", "error": f"fetch {s}: {b[:200]}"})
            continue
        cur = (json.loads(b).get("fields", {}) or {}).get("summary", "")
        if cur.startswith("(DEPRECATED)"):
            print(f"  {ek}: already deprecated -> {cur[:80]}")
            results.append({"key": ek, "phase": "deprecate_epics", "result": "ok"})
            continue
        new_sum = f"(DEPRECATED) {cur}"
        if dry_run:
            print(f"  {ek}: [dry-run] would set summary -> {new_sum[:80]}")
            continue
        us, ub = call("PUT", f"/rest/api/3/issue/{ek}",
                      body={"fields": {"summary": new_sum}})
        if 200 <= us < 300:
            print(f"  {ek}: {new_sum[:80]}")
            results.append({"key": ek, "phase": "deprecate_epics", "result": "ok"})
        else:
            print(f"  {ek}: PUT failed {us} {ub[:200]}")
            results.append({"key": ek, "phase": "deprecate_epics",
                            "result": "failed", "error": f"{us}: {ub[:200]}"})
    return results


def phase_delete_epics(call, dry_run=False):
    print("\n=== DELETE OBSOLETE EPICS ===")
    results = []
    for ek in OBSOLETE_EPIC_KEYS:
        # Verify the epic still exists; 404 means already deleted -> idempotent ok
        es, _ = call("GET", f"/rest/api/3/issue/{ek}?fields=summary")
        if es == 404:
            print(f"  {ek}: already deleted")
            results.append({"key": ek, "phase": "delete_epics", "result": "ok"})
            continue
        jql = f"parent = {ek}"
        path = "/rest/api/3/search/jql?" + urlencode(
            {"jql": jql, "fields": "summary", "maxResults": 100})
        s, b = call("GET", path)
        if s != 200:
            print(f"  {ek}: child-query failed {s}")
            results.append({"key": ek, "phase": "delete_epics",
                            "result": "failed", "error": f"child query {s}: {b[:200]}"})
            continue
        data = json.loads(b)
        children = data.get("issues", [])
        if children:
            child_keys = [c["key"] for c in children]
            print(f"  {ek}: SKIP — still has {len(child_keys)} children: {child_keys[:5]}")
            results.append({"key": ek, "phase": "delete_epics",
                            "result": "skipped_has_children", "children": child_keys})
            continue
        if dry_run:
            print(f"  {ek}: [dry-run] would DELETE")
            continue
        ds, db = call("DELETE", f"/rest/api/3/issue/{ek}")
        if 200 <= ds < 300 or ds == 204:
            print(f"  {ek}: DELETED")
            results.append({"key": ek, "phase": "delete_epics", "result": "ok"})
        else:
            print(f"  {ek}: DELETE failed {ds} {db[:200]}")
            results.append({"key": ek, "phase": "delete_epics",
                            "result": "failed", "error": f"{ds}: {db[:200]}"})
    return results


def phase_verify(call):
    print("\n=== VERIFY ===")
    phase_audit(call)
    diff = phase_diff()
    print("\nResidual deltas:")
    print(f"  labels:      {len(diff['label_ops'])}")
    print(f"  transitions: {len(diff['transition_ops'])}")
    print(f"  resolutions: {len(diff['resolution_ops'])}")
    print(f"  assignees:   {len(diff['assignee_ops'])}")
    print(f"  parents:     {len(diff['parent_ops'])}")


# ---------- main ----------
def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--phase", default="all",
        help="comma-sep: audit,diff,annotate_worksheet,labels,transitions,"
             "resolutions,assignees,parents,deprecate_epics,delete_epics,"
             "empty_backlog,verify,all",
    )
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    env = load_env()
    base = env["ATLASSIAN_BASE_URL"].rstrip("/")
    auth = "Basic " + b64encode(
        f"{env['ATLASSIAN_EMAIL']}:{env['ATLASSIAN_API_TOKEN']}".encode()
    ).decode()
    call = make_call(base, auth)

    if args.phase == "all":
        phases = ["audit", "diff", "annotate_worksheet", "labels",
                  "transitions", "resolutions", "assignees", "parents",
                  "deprecate_epics", "delete_epics", "empty_backlog", "verify"]
    else:
        phases = [p.strip() for p in args.phase.split(",")]

    all_results = []
    for p in phases:
        if p == "audit":
            phase_audit(call)
        elif p == "diff":
            phase_diff()
        elif p == "annotate_worksheet":
            phase_annotate_worksheet()
        elif p == "labels":
            all_results += phase_labels(call, args.dry_run)
        elif p == "transitions":
            all_results += phase_transitions(call, args.dry_run)
        elif p == "resolutions":
            all_results += phase_resolutions(call, args.dry_run)
        elif p == "assignees":
            all_results += phase_assignees(call, args.dry_run)
        elif p == "parents":
            all_results += phase_parents(call, args.dry_run)
        elif p == "deprecate_epics":
            all_results += phase_deprecate_epics(call, args.dry_run)
        elif p == "delete_epics":
            all_results += phase_delete_epics(call, args.dry_run)
        elif p == "empty_backlog":
            all_results += phase_empty_backlog(call, args.dry_run)
        elif p == "verify":
            phase_verify(call)
        else:
            print(f"Unknown phase: {p}")
            sys.exit(1)

    if all_results:
        counts = Counter((r["phase"], r["result"]) for r in all_results)
        print("\n=== OVERALL SUMMARY ===")
        for (ph, res), n in sorted(counts.items()):
            print(f"  {ph:14s} {res:25s}: {n}")
        failures = [r for r in all_results
                    if r["result"] not in ("ok", "fallback_done", "skipped_has_children")]
        print(f"\nFailures/skips: {len(failures)}")
        for fl in failures[:30]:
            msg = fl.get("error") or fl.get("result")
            print(f"  {fl.get('key','-'):14s} {fl['phase']:14s} {str(msg)[:140]}")
        with open(LOG_PATH, "w") as f:
            json.dump({"results": all_results,
                       "counts": {f"{k[0]}__{k[1]}": v for k, v in counts.items()}},
                      f, indent=2)
        print(f"\nFull log: {LOG_PATH}")


if __name__ == "__main__":
    main()
