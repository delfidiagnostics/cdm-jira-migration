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
WORKSHEET = ROOT / "CDM_TRIAGE_WORKSHEET.csv"
MAPPING = ROOT / "CDM_TESTCDM_KEY_MAPPING.csv"

# Per-project config. TESTCDM is the dry-run clone whose new-taxonomy epics already
# exist (630-637), so it needs no preflight. CDM is production: its 6 epics are
# created/renamed at runtime by phase_preflight_epics. The 5 Era-3 epics
# (CDM-676..680) are empty shells; 3 are renamed to survive, 2 are folded into
# `obsolete` for deletion. `map_identity` means worksheet key == live key (no clone
# mapping CSV); TESTCDM goes through CDM_TESTCDM_KEY_MAPPING.csv instead.
EPIC_CONFIG = {
    "TESTCDM": {
        "map_identity": False,
        "preserve": {
            "TESTCDM-630", "TESTCDM-631", "TESTCDM-633",
            "TESTCDM-635", "TESTCDM-636", "TESTCDM-637",
        },
        "obsolete": [
            "TESTCDM-1", "TESTCDM-29", "TESTCDM-282",
            "TESTCDM-521", "TESTCDM-632", "TESTCDM-634",
        ],
        "preflight_rename": {},   # epics already exist; nothing to rename
        "preflight_create": [],   # nothing to create
    },
    "CDM": {
        "map_identity": True,
        "preserve": {"CDM-676", "CDM-677", "CDM-679"},  # renamed Era-3 survivors
        "obsolete": [
            "CDM-46",   # Corporate Goals
            "CDM-79",   # DELFI-L101
            "CDM-80",   # DELFI-L201
            "CDM-467",  # DELFI-L301
            "CDM-678",  # empty Era-3 epic, consolidated into PMA
            "CDM-680",  # empty Era-3 epic, consolidated into 4ITLR
        ],
        "preflight_rename": {
            "CDM-676": "CASCADE (L201) Readout",
            "CDM-677": "IVD Lung PMA Submission",
            "CDM-679": "4ITLR Readout",
        },
        "preflight_create": [
            "Reimbursement & Clinical Evidence",
            "Departmental Ops",
            "Pre-2026 Legacy",
        ],
    },
}

# Active project + derived globals. Defaults to TESTCDM; main() overrides from
# --project and recomputes the project-scoped state dir + epic key sets.
PROJECT = "TESTCDM"
STATE_DIR = ROOT / f".{PROJECT.lower()}-current"
REMAINING_OPS = ROOT / "remaining_ops.json"
LOG_PATH = ROOT / "cdm_migration_log.json"
PRESERVE_EPIC_KEYS = EPIC_CONFIG[PROJECT]["preserve"]
OBSOLETE_EPIC_KEYS = EPIC_CONFIG[PROJECT]["obsolete"]

# Worksheet proposed_status -> (acceptable workflow status names, transition id).
# Canonical taxonomy uses Done/Dismissed (matching Jira's workflow names — we
# chose not to rename). `Completed`/`Cancelled` are kept as aliases so older
# worksheet copies still work.
# Transition IDs: 31/51/11 are stable across projects. In Progress is 21 on CDM
# but became 2 on TESTCDM after the workflow edit that added Ongoing — neither
# is hit on the current run because no row needs an In Progress transition.
# Ongoing status (id 11266) is project-scoped to TESTCDM. On CDM this transition
# won't exist until the workflow is edited similarly; zero worksheet rows use
# Ongoing anyway, so the mapping is dormant in practice.
STATUS_TO_TRANSITION = {
    "Done": (("Done", "Completed"), "31"),
    "Completed": (("Done", "Completed"), "31"),  # alias
    "Dismissed": (("Dismissed", "Cancelled"), "51"),
    "Cancelled": (("Dismissed", "Cancelled"), "51"),  # alias
    "In Progress": (("In Progress",), "21"),
    "Ongoing": (("Ongoing", "In Progress"), "3"),
    "To Do": (("To Do",), None),
}

RESOLUTION_BY_PROPOSED_STATUS = {
    "Done": "Done",
    "Completed": "Done",     # alias
    "Dismissed": "Won't Do",
    "Cancelled": "Won't Do",  # alias
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
    print(f"Audited {len(audit)} {PROJECT} issues in {time.time()-t0:.1f}s")
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
    # CDM (production) runs on its own keys — identity, no clone mapping CSV.
    if EPIC_CONFIG[PROJECT].get("map_identity"):
        return None
    cdm_to_test = {}
    with open(MAPPING) as f:
        for r in csv.DictReader(f):
            if r["cdm_key"]:
                cdm_to_test[r["cdm_key"]] = r["testcdm_key"]
    return cdm_to_test


def map_key(mapping, cdm_key):
    """Resolve a worksheet (CDM) key to the live key in the target project.
    Identity for CDM (mapping is None); via the clone CSV for TESTCDM."""
    return cdm_key if mapping is None else mapping.get(cdm_key)


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
        tk = map_key(cdm_to_test, cdm_key)
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

        # Goal workflow has no Dismissed transition; fallback lands at
        # Done + Won't Do. Treat that as satisfied for proposed_status "Dismissed"
        # (or its alias "Cancelled" from older worksheet copies).
        goal_cancelled_satisfied = (
            is_goal
            and proposed_status in ("Dismissed", "Cancelled")
            and cur.get("status") in ("Done", "Completed")
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


def _resolve_task_issuetype_id(call):
    """Return (project_id, standard 'Task' issuetype id) for PROJECT. The Task id
    is project-specific (11035 on TESTCDM, differs on CDM) so it's resolved at
    runtime rather than hardcoded."""
    s, b = call("GET", f"/rest/api/3/project/{PROJECT}")
    if s != 200:
        return None, None
    pid = json.loads(b)["id"]
    s, b = call("GET", "/rest/api/3/issue/createmeta?" + urlencode(
        {"projectKeys": PROJECT, "expand": "projects.issuetypes"}))
    if s != 200:
        return pid, None
    for p in json.loads(b).get("projects", []):
        for it in p.get("issuetypes", []):
            if it.get("name") == "Task" and not it.get("subtask"):
                return pid, it["id"]
    return pid, None


def phase_convert_subtasks(call, dry_run=False):
    """Convert rows the worksheet marks as a standard type (Task) but that are
    still live Subtasks. Jira refuses to cross the subtask/standard boundary via a
    field edit (`PUT issuetype` -> 400 "issue type selected is invalid"); the
    bulk-move API is the only path. Two steps per ticket:
      1. POST /rest/api/3/bulk/issues/move  (async; also drops the subtask parent)
      2. PUT  parent = epic                 (now standard, re-parent to its epic)
    Must run before phase_parents. Idempotent: a row already live as a standard
    type is no longer a candidate, so a re-run is a no-op."""
    print("\n=== CONVERT SUBTASKS -> TASKS ===")
    audit = load_audit()
    worksheet = load_worksheet()
    cdm_to_test = load_mapping()
    epic_name_to_key = build_epic_name_to_key(audit)
    skip_keys = PRESERVE_EPIC_KEYS | set(OBSOLETE_EPIC_KEYS)

    candidates = []
    for row in worksheet:
        if (row.get("type") or "").lower() in SUBTASK_TYPES:
            continue
        tk = map_key(cdm_to_test, row["key"])
        if not tk or tk in skip_keys:
            continue
        cur = audit.get(tk)
        if cur and (cur.get("issuetype") or "").lower() in SUBTASK_TYPES:
            candidates.append((tk, row))

    if not candidates:
        print("  convert_subtasks: nothing to do")
        return []

    print(f"  {len(candidates)} subtask(s) to convert -> Task:")
    for tk, row in candidates:
        epic = epic_name_to_key.get((row.get("proposed_epic") or "").strip())
        print(f"    {tk} ({row['key']}) -> Task, parent={epic or '(epic unresolved)'}")
    if dry_run:
        print("  [dry-run] no conversion performed")
        return []

    pid, task_type_id = _resolve_task_issuetype_id(call)
    if not pid or not task_type_id:
        print("  could not resolve project id / Task issuetype id; aborting phase")
        return [{"key": "-", "phase": "convert_subtasks", "result": "failed",
                 "error": "issuetype/project resolution"}]

    move_keys = [tk for tk, _ in candidates]
    payload = {"sendBulkNotification": False, "targetToSourcesMapping": {
        f"{pid},{task_type_id}": {
            "inferClassificationDefaults": True, "inferFieldDefaults": True,
            "inferStatusDefaults": True, "inferSubtaskTypeDefault": True,
            "issueIdsOrKeys": move_keys}}}
    s, b = call("POST", "/rest/api/3/bulk/issues/move", body=payload)
    if not (200 <= s < 300):
        print(f"  bulk move failed: {s} {b[:200]}")
        return [{"key": ",".join(move_keys), "phase": "convert_subtasks",
                 "result": "failed", "error": f"bulk move {s}: {b[:200]}"}]
    task_id = json.loads(b).get("taskId")
    print(f"  bulk move submitted (taskId={task_id}); polling...")
    final = None
    for _ in range(30):
        ts, tb = call("GET", f"/rest/api/3/task/{task_id}")
        final = json.loads(tb).get("status") if 200 <= ts < 300 else None
        if final == "COMPLETE":
            break
        if final in ("FAILED", "CANCELLED", "DEAD"):
            print(f"  bulk move task ended {final}: {tb[:200]}")
            return [{"key": ",".join(move_keys), "phase": "convert_subtasks",
                     "result": "failed", "error": f"task {final}"}]
        time.sleep(2)
    if final != "COMPLETE":
        print("  bulk move did not complete in time")
        return [{"key": ",".join(move_keys), "phase": "convert_subtasks",
                 "result": "failed", "error": "task poll timeout"}]

    results = []
    for tk, row in candidates:
        epic = epic_name_to_key.get((row.get("proposed_epic") or "").strip())
        if not epic:
            results.append({"key": tk, "phase": "convert_subtasks",
                            "result": "converted_no_epic"})
            continue
        ps, pb = call("PUT", f"/rest/api/3/issue/{tk}",
                      body={"fields": {"parent": {"key": epic}}})
        if 200 <= ps < 300:
            results.append({"key": tk, "phase": "convert_subtasks", "result": "ok"})
        else:
            results.append({"key": tk, "phase": "convert_subtasks",
                            "result": "failed", "error": f"reparent {ps}: {pb[:200]}"})
    ok = sum(1 for r in results if r["result"] == "ok")
    print(f"  converted {ok}/{len(candidates)} subtask(s) -> Task under epic")
    return results


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


def phase_preflight_epics(call, dry_run=False):
    """Create + rename the 6 new-taxonomy epics so build_epic_name_to_key can
    resolve them at diff time. Plan is per-project in EPIC_CONFIG:
      - rename: existing epic key -> new summary (e.g. CDM-676 -> CASCADE …)
      - create: fresh Epic for each name not already present
    Idempotent: a rename whose target already matches is skipped; a create whose
    name already exists is skipped. The leftover empty Era-3 epics are removed by
    phase_delete_epics (they're listed in `obsolete`). Runs before audit so the
    renames/creates are visible to every downstream phase. No-op for TESTCDM."""
    print("\n=== PREFLIGHT EPICS (create + rename) ===")
    cfg = EPIC_CONFIG.get(PROJECT, {})
    renames = cfg.get("preflight_rename", {})
    creates = cfg.get("preflight_create", [])
    if not renames and not creates:
        print(f"  no epic preflight configured for {PROJECT}; nothing to do")
        return []

    # Live epics by summary -> key (for create-idempotency).
    s, b = call("GET", "/rest/api/3/search/jql?" + urlencode(
        {"jql": f"project = {PROJECT} AND issuetype = Epic",
         "fields": "summary", "maxResults": 100}))
    live = {}
    if s == 200:
        for i in json.loads(b).get("issues", []):
            live[(i.get("fields") or {}).get("summary") or ""] = i["key"]
    else:
        print(f"  WARN: epic listing failed {s}; create-idempotency degraded")

    results = []
    for key, new_name in renames.items():
        gs, gb = call("GET", f"/rest/api/3/issue/{key}?fields=summary")
        if gs == 404:
            print(f"  {key}: NOT FOUND — cannot rename to {new_name!r}")
            results.append({"key": key, "phase": "preflight_epics",
                            "result": "failed", "error": "404 not found"})
            continue
        cur = (json.loads(gb).get("fields") or {}).get("summary") if gs == 200 else None
        if cur == new_name:
            print(f"  {key}: already named {new_name!r}")
            results.append({"key": key, "phase": "preflight_epics", "result": "ok"})
            continue
        if dry_run:
            print(f"  {key}: [dry-run] rename {cur!r} -> {new_name!r}")
            continue
        ps, pb = call("PUT", f"/rest/api/3/issue/{key}",
                      body={"fields": {"summary": new_name}})
        if 200 <= ps < 300:
            print(f"  {key}: renamed -> {new_name!r}")
            results.append({"key": key, "phase": "preflight_epics", "result": "ok"})
        else:
            print(f"  {key}: rename failed {ps} {pb[:200]}")
            results.append({"key": key, "phase": "preflight_epics",
                            "result": "failed", "error": f"{ps}: {pb[:200]}"})

    for name in creates:
        if name in live:
            print(f"  create {name!r}: already exists ({live[name]})")
            results.append({"key": live[name], "phase": "preflight_epics", "result": "ok"})
            continue
        if dry_run:
            print(f"  create {name!r}: [dry-run] would create Epic in {PROJECT}")
            continue
        cs, cb = call("POST", "/rest/api/3/issue", body={"fields": {
            "project": {"key": PROJECT}, "issuetype": {"name": "Epic"},
            "summary": name}})
        if 200 <= cs < 300:
            newk = json.loads(cb).get("key")
            print(f"  create {name!r}: created {newk}")
            results.append({"key": newk, "phase": "preflight_epics", "result": "ok"})
        else:
            print(f"  create {name!r}: failed {cs} {cb[:200]}")
            results.append({"key": name, "phase": "preflight_epics",
                            "result": "failed", "error": f"{cs}: {cb[:200]}"})
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
        help="comma-sep: preflight_epics,audit,diff,annotate_worksheet,labels,"
             "transitions,resolutions,assignees,convert_subtasks,parents,"
             "deprecate_epics,delete_epics,empty_backlog,verify,all",
    )
    parser.add_argument("--project", default="TESTCDM", choices=sorted(EPIC_CONFIG),
                        help="target project: TESTCDM (dry-run clone) or CDM (production)")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    global PROJECT, STATE_DIR, PRESERVE_EPIC_KEYS, OBSOLETE_EPIC_KEYS
    PROJECT = args.project
    STATE_DIR = ROOT / f".{PROJECT.lower()}-current"
    PRESERVE_EPIC_KEYS = EPIC_CONFIG[PROJECT]["preserve"]
    OBSOLETE_EPIC_KEYS = EPIC_CONFIG[PROJECT]["obsolete"]
    print(f"Project: {PROJECT}  (state dir: {STATE_DIR.name})")

    env = load_env()
    base = env["ATLASSIAN_BASE_URL"].rstrip("/")
    auth = "Basic " + b64encode(
        f"{env['ATLASSIAN_EMAIL']}:{env['ATLASSIAN_API_TOKEN']}".encode()
    ).decode()
    call = make_call(base, auth)

    if args.phase == "all":
        phases = ["preflight_epics", "audit", "diff", "annotate_worksheet",
                  "labels", "transitions", "resolutions", "assignees",
                  "convert_subtasks", "parents", "deprecate_epics",
                  "delete_epics", "empty_backlog", "verify"]
    else:
        phases = [p.strip() for p in args.phase.split(",")]

    all_results = []
    for p in phases:
        if p == "preflight_epics":
            all_results += phase_preflight_epics(call, args.dry_run)
        elif p == "audit":
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
        elif p == "convert_subtasks":
            all_results += phase_convert_subtasks(call, args.dry_run)
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
