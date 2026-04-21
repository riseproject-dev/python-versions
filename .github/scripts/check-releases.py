#!/usr/bin/env python3
"""Scan python/cpython tags and dispatch build-python.yml for any not yet released.

Reads ``GH_TOKEN``, ``GITHUB_REPOSITORY``, ``MAX_PARALLELISM`` and ``DRY_RUN``
from the environment. For each CPython ``v3.Y.Z[a|b|rc]N`` tag from 3.10
onward, checks whether a matching release (tag ``<normalised>-<run_id>``)
already exists here, and if not, dispatches the ``build-python.yml`` workflow.
Free-threaded is enabled automatically for minor >= 13.

``MAX_PARALLELISM`` is the number of build-python.yml runs to have in flight
concurrently. Each worker dispatches a run, waits for it to finish, then moves
on to the next candidate.
"""

from __future__ import annotations

import concurrent.futures as cf
import datetime
import json
import os
import re
import sys
import threading
import time
import urllib.error
import urllib.request

API_ROOT = "https://api.github.com"
REF_PREFIX = "refs/tags/"
CPYTHON_TAG_RE = re.compile(
    r"^v(?P<full_minor>3\.(?:1[0-9]|[2-9][0-9]))\.(?P<patch>[0-9]+)"
    r"(?P<pre>a[0-9]+|b[0-9]+|rc[0-9]+)?$"
)
PRE_SUBS = (
    (re.compile(r"a([0-9]+)$"), r"-alpha.\1"),
    (re.compile(r"b([0-9]+)$"), r"-beta.\1"),
    (re.compile(r"rc([0-9]+)$"), r"-rc.\1"),
)
BUILD_WORKFLOW = "build-python.yml"

# CPython tags we know don't build on riscv64. Skipped silently after the
# release-existence check fails.
DENYLIST: frozenset[str] = frozenset({
    "v3.13.0a2",
    "v3.13.0b3",
})

# Poll cadence (seconds)
RUN_DISCOVERY_POLL = 5
RUN_DISCOVERY_ATTEMPTS = 60  # up to 5 minutes
RUN_STATUS_POLL = 30

_claim_lock = threading.Lock()
_claimed_run_ids: set[int] = set()
_print_lock = threading.Lock()


def log(msg: str) -> None:
    with _print_lock:
        print(msg, flush=True)


def _request(url: str, token: str, method: str = "GET", body: bytes | None = None) -> urllib.request.Request:
    return urllib.request.Request(
        url,
        data=body,
        method=method,
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "Content-Type": "application/json",
            "User-Agent": "check-releases-script",
        },
    )


def gh_get_paginated(path: str, token: str) -> list[dict]:
    """Paginate a GitHub REST API list endpoint."""
    results: list[dict] = []
    url: str | None = f"{API_ROOT}{path}"
    sep = "&" if "?" in url else "?"
    url = f"{url}{sep}per_page=100"
    while url:
        with urllib.request.urlopen(_request(url, token)) as resp:
            results.extend(json.load(resp))
            link = resp.headers.get("Link", "")
        url = _next_link(link)
    return results


def gh_get_json(path: str, token: str) -> dict:
    """Single GET returning parsed JSON."""
    with urllib.request.urlopen(_request(f"{API_ROOT}{path}", token)) as resp:
        return json.load(resp)


def _next_link(link_header: str) -> str | None:
    for part in link_header.split(","):
        section = part.strip()
        if section.endswith('rel="next"'):
            return section.split(";", 1)[0].strip().lstrip("<").rstrip(">")
    return None


def normalise(cpython_tag: str) -> str:
    version = cpython_tag.lstrip("v")
    for pattern, repl in PRE_SUBS:
        version = pattern.sub(repl, version)
    return version


def version_sort_key(cpython_tag: str) -> tuple[int, int, int, int, int]:
    match = CPYTHON_TAG_RE.match(cpython_tag)
    assert match is not None
    minor = int(match.group("full_minor").split(".")[1])
    patch = int(match.group("patch"))
    pre = match.group("pre")
    if pre is None:
        stage_order, stage_num = 3, 0
    elif pre.startswith("a"):
        stage_order, stage_num = 0, int(pre[1:])
    elif pre.startswith("b"):
        stage_order, stage_num = 1, int(pre[1:])
    else:
        stage_order, stage_num = 2, int(pre[2:])
    return (3, minor, patch, stage_order, stage_num)


def dispatch_workflow(repo: str, token: str, cpython_tag: str) -> None:
    body = json.dumps(
        {
            "ref": "main",
            "inputs": {
                "cpython_tag": cpython_tag,
            },
        }
    ).encode("utf-8")
    url = f"{API_ROOT}/repos/{repo}/actions/workflows/{BUILD_WORKFLOW}/dispatches"
    with urllib.request.urlopen(_request(url, token, method="POST", body=body)) as resp:
        resp.read()


def find_dispatched_run(repo: str, token: str, baseline: set[int], after_ts: str) -> dict | None:
    """Find a workflow run dispatched after ``after_ts`` that isn't baselined or claimed.

    Called once per dispatch. Claims the oldest eligible run so that concurrent
    workers each grab a distinct run_id.
    """
    path = f"/repos/{repo}/actions/workflows/{BUILD_WORKFLOW}/runs?per_page=50&event=workflow_dispatch"
    for _ in range(RUN_DISCOVERY_ATTEMPTS):
        time.sleep(RUN_DISCOVERY_POLL)
        data = gh_get_json(path, token)
        runs = sorted(data.get("workflow_runs", []), key=lambda r: r["created_at"])
        with _claim_lock:
            for r in runs:
                rid = r["id"]
                if rid in baseline:
                    continue
                if rid in _claimed_run_ids:
                    continue
                if r["created_at"] < after_ts:
                    continue
                _claimed_run_ids.add(rid)
                return r
    return None


def wait_run_complete(repo: str, token: str, run_id: int) -> str | None:
    path = f"/repos/{repo}/actions/runs/{run_id}"
    while True:
        time.sleep(RUN_STATUS_POLL)
        data = gh_get_json(path, token)
        if data.get("status") == "completed":
            return data.get("conclusion")


def run_build(repo: str, token: str, tag: str, baseline: set[int]) -> tuple[str, str | None]:
    log(f"[{tag}] dispatching build-python.yml")
    after_ts = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    dispatch_workflow(repo, token, tag)
    run = find_dispatched_run(repo, token, baseline, after_ts)
    if run is None:
        log(f"[{tag}] WARN: could not locate dispatched run within timeout")
        return tag, None
    log(f"[{tag}] run {run['id']}: {run['html_url']}")
    conclusion = wait_run_complete(repo, token, run["id"])
    log(f"[{tag}] run {run['id']} completed: {conclusion}")
    return tag, conclusion


def parse_bool(value: str | None) -> bool:
    return (value or "").strip().lower() in {"1", "true", "yes", "y"}


def main() -> int:
    token = os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN")
    repo = os.environ.get("GITHUB_REPOSITORY")
    if not token or not repo:
        print("GH_TOKEN and GITHUB_REPOSITORY must be set", file=sys.stderr)
        return 1
    try:
        max_parallelism = int(os.environ.get("MAX_PARALLELISM", "4"))
    except ValueError:
        print("MAX_PARALLELISM must be an integer", file=sys.stderr)
        return 1
    if max_parallelism < 1:
        print("MAX_PARALLELISM must be >= 1", file=sys.stderr)
        return 1
    dry_run = parse_bool(os.environ.get("DRY_RUN"))
    force_rebuild = parse_bool(os.environ.get("FORCE_REBUILD", "false"))

    # Fetch all v* tags from python/cpython
    refs = gh_get_paginated("/repos/python/cpython/git/matching-refs/tags/v", token)
    candidates: list[str] = []
    for entry in refs:
        ref = entry.get("ref", "")
        if not ref.startswith(REF_PREFIX):
            continue
        tag = ref[len(REF_PREFIX):]
        if CPYTHON_TAG_RE.match(tag):
            candidates.append(tag)
    candidates.sort(key=version_sort_key, reverse=True)

    # Existing release tags
    releases = gh_get_paginated(f"/repos/{repo}/releases", token)
    existing_tags = {r.get("tag_name") or "" for r in releases}

    # Decide what to dispatch
    to_dispatch: list[str] = []
    for tag in candidates:
        match = CPYTHON_TAG_RE.match(tag)
        assert match is not None
        minor = int(match.group("full_minor").split(".")[1])
        normalised = normalise(tag)
        log(f"Tag {tag} -> release {normalised}")

        version_re = re.compile(rf"^{re.escape(normalised)}-[0-9]+$")
        if any(version_re.match(t) for t in existing_tags):
            if force_rebuild:
                log("  already released, force rebuilding anyway")
                # fall through
            else:
                log("  already released, skipping")
                continue

        if tag in DENYLIST:
            log("  in denylist, skipping")
            continue

        log(f"  queued for dispatch")
        to_dispatch.append(tag)

    log("")
    log(f"=== {len(to_dispatch)} candidate(s) to build ===")
    for tag in to_dispatch:
        log(f"  {tag}")

    if dry_run:
        log("")
        log("DRY_RUN=true: not dispatching anything")
        return 0

    if not to_dispatch:
        return 0

    # Baseline of existing build-python.yml runs so we can identify newly-created ones
    baseline_data = gh_get_json(
        f"/repos/{repo}/actions/workflows/{BUILD_WORKFLOW}/runs?per_page=100", token
    )
    baseline = {r["id"] for r in baseline_data.get("workflow_runs", [])}

    log("")
    log(f"=== Running up to {max_parallelism} build(s) in parallel ===")
    failures: list[str] = []
    with cf.ThreadPoolExecutor(max_workers=max_parallelism) as pool:
        futures = [
            pool.submit(run_build, repo, token, tag, baseline)
            for tag in to_dispatch
        ]
        for fut in cf.as_completed(futures):
            try:
                tag, conclusion = fut.result()
            except Exception as exc:  # noqa: BLE001
                log(f"task raised: {exc}")
                failures.append(str(exc))
                continue
            if conclusion != "success":
                failures.append(f"{tag}: {conclusion}")

    log("")
    log(f"=== Finished: {len(to_dispatch) - len(failures)}/{len(to_dispatch)} succeeded ===")
    if failures:
        for f in failures:
            log(f"  FAIL {f}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
