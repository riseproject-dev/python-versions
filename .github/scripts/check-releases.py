#!/usr/bin/env python3
"""Scan python/cpython tags and dispatch build-python.yml for any not yet released.

Reads ``GH_TOKEN``, ``GITHUB_REPOSITORY`` and ``MAX_DISPATCHES`` from the
environment. For each CPython ``v3.Y.Z[a|b|rc]N`` tag from 3.10 onward, checks
whether a matching release (tag ``<normalised>-<run_id>``) already exists here,
and if not, dispatches the ``build-python.yml`` workflow. Free-threaded is
enabled automatically for minor >= 13.
"""

from __future__ import annotations

import json
import os
import re
import sys
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


def gh_get(path: str, token: str) -> list[dict]:
    """Paginate a GitHub REST API list endpoint."""
    results: list[dict] = []
    url: str | None = f"{API_ROOT}{path}"
    sep = "&" if "?" in url else "?"
    url = f"{url}{sep}per_page=100"
    while url:
        req = urllib.request.Request(
            url,
            headers={
                "Authorization": f"Bearer {token}",
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
                "User-Agent": "check-releases-script",
            },
        )
        with urllib.request.urlopen(req) as resp:
            results.extend(json.load(resp))
            link = resp.headers.get("Link", "")
        url = _next_link(link)
    return results


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


def dispatch_workflow(repo: str, token: str, cpython_tag: str, ft: bool) -> None:
    body = json.dumps(
        {
            "ref": "main",
            "inputs": {
                "cpython_tag": cpython_tag,
                "freethreaded": "true" if ft else "false",
            },
        }
    ).encode("utf-8")
    req = urllib.request.Request(
        f"{API_ROOT}/repos/{repo}/actions/workflows/build-python.yml/dispatches",
        data=body,
        method="POST",
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "Content-Type": "application/json",
            "User-Agent": "check-releases-script",
        },
    )
    with urllib.request.urlopen(req) as resp:
        resp.read()


def main() -> int:
    token = os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN")
    repo = os.environ.get("GITHUB_REPOSITORY")
    if not token or not repo:
        print("GH_TOKEN and GITHUB_REPOSITORY must be set", file=sys.stderr)
        return 1
    try:
        max_dispatches = int(os.environ.get("MAX_DISPATCHES", "20"))
    except ValueError:
        print("MAX_DISPATCHES must be an integer", file=sys.stderr)
        return 1

    # Fetch all v* tags from python/cpython
    refs = gh_get("/repos/python/cpython/git/matching-refs/tags/v", token)
    candidates: list[str] = []
    for entry in refs:
        ref = entry.get("ref", "")
        if not ref.startswith(REF_PREFIX):
            continue
        tag = ref[len(REF_PREFIX):]
        if CPYTHON_TAG_RE.match(tag):
            candidates.append(tag)
    # Sort newest first
    candidates.sort(key=version_sort_key, reverse=True)

    # Fetch all existing release tags on this repo
    releases = gh_get(f"/repos/{repo}/releases", token)
    existing_tags = {r.get("tag_name") or "" for r in releases}

    dispatched = 0
    dispatched_list: list[str] = []

    for tag in candidates:
        match = CPYTHON_TAG_RE.match(tag)
        assert match is not None
        minor = int(match.group("full_minor").split(".")[1])
        normalised = normalise(tag)
        print(f"Tag {tag} -> release {normalised}")

        version_re = re.compile(rf"^{re.escape(normalised)}-[0-9]+$")
        if any(version_re.match(t) for t in existing_tags):
            print("  already released, skipping")
            continue

        if dispatched >= max_dispatches:
            print("  would dispatch (cap reached)")
            continue

        ft = minor >= 13
        print(f"  dispatching build-python.yml (freethreaded={str(ft).lower()})")
        dispatch_workflow(repo, token, tag, ft)
        dispatched += 1
        dispatched_list.append(f"{tag} -> {normalised} (ft={str(ft).lower()})")

    print("")
    print(f"=== Dispatched {dispatched} build(s) ===")
    for line in dispatched_list:
        print(line)
    return 0


if __name__ == "__main__":
    sys.exit(main())
