#!/usr/bin/env python3
"""Regenerate versions-manifest.json from the repository's GitHub releases.

Reads `GH_TOKEN` and `GITHUB_REPOSITORY` from the environment and paginates
through every release on the repo. Each release is expected to have a tag of
the form ``<normalised-version>-<run_id>`` (e.g. ``3.15.0-alpha.7-22913288817``).
For each distinct version the release with the largest ``run_id`` wins, and an
entry is emitted in the manifest with its assets.

Output matches the schema used by actions/python-versions.
"""

from __future__ import annotations

import json
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request

API_ROOT = "https://api.github.com"
TAG_RE = re.compile(r"^(?P<version>.+)-(?P<run_id>\d+)$")
PRERELEASE_RE = re.compile(r"-(alpha|beta|rc)\.(\d+)$")
FILENAME_PLATFORM_RE = re.compile(
    r"-linux-(?P<platform_version>\d+\.\d+)-(?P<arch>[^.]+?)(?P<ft>-freethreaded)?\.tar\.gz$"
)


def gh_get(path: str, token: str) -> list[dict]:
    """Paginate a GitHub REST API list endpoint."""
    results: list[dict] = []
    url: str | None = f"{API_ROOT}{path}"
    if "?" in url:
        url += "&per_page=100"
    else:
        url += "?per_page=100"
    while url:
        req = urllib.request.Request(
            url,
            headers={
                "Authorization": f"Bearer {token}",
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
                "User-Agent": "update-manifest-script",
            },
        )
        with urllib.request.urlopen(req) as resp:
            payload = json.load(resp)
            results.extend(payload)
            link = resp.headers.get("Link", "")
        url = _next_link(link)
    return results


def _next_link(link_header: str) -> str | None:
    for part in link_header.split(","):
        section = part.strip()
        if section.endswith('rel="next"'):
            return section.split(";", 1)[0].strip().lstrip("<").rstrip(">")
    return None


def version_sort_key(version: str) -> tuple[int, int, int, int, int]:
    """Return a tuple suitable for descending version sort.

    Order: (major, minor, patch, stage_order, stage_num)
    where stage_order is 0=alpha, 1=beta, 2=rc, 3=final.
    """
    match = PRERELEASE_RE.search(version)
    if match:
        stage_map = {"alpha": 0, "beta": 1, "rc": 2}
        stage_order = stage_map[match.group(1)]
        stage_num = int(match.group(2))
        base = version[: match.start()]
    else:
        stage_order = 3
        stage_num = 0
        base = version
    parts = base.split(".")
    major = int(parts[0])
    minor = int(parts[1]) if len(parts) > 1 else 0
    patch = int(parts[2]) if len(parts) > 2 else 0
    return (major, minor, patch, stage_order, stage_num)


def is_stable(version: str) -> bool:
    return PRERELEASE_RE.search(version) is None


def build_file_entry(asset: dict) -> dict | None:
    filename = asset["name"]
    match = FILENAME_PLATFORM_RE.search(filename)
    if not match:
        return None
    arch = match.group("arch")
    if match.group("ft"):
        arch = f"{arch}-freethreaded"
    return {
        "filename": filename,
        "arch": arch,
        "platform": "linux",
        "platform_version": match.group("platform_version"),
        "download_url": asset["browser_download_url"],
    }


def main() -> int:
    token = os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN")
    repo = os.environ.get("GITHUB_REPOSITORY")
    if not token or not repo:
        print("GH_TOKEN and GITHUB_REPOSITORY must be set", file=sys.stderr)
        return 1

    releases = gh_get(f"/repos/{repo}/releases", token)

    # Pick the release with the highest run_id per version.
    best: dict[str, dict] = {}
    for release in releases:
        if release.get("draft"):
            continue
        tag = release.get("tag_name") or ""
        match = TAG_RE.match(tag)
        if not match:
            continue
        version = match.group("version")
        run_id = int(match.group("run_id"))
        existing = best.get(version)
        if existing is None or run_id > existing["_run_id"]:
            best[version] = {"_run_id": run_id, "release": release}

    entries: list[dict] = []
    for version, picked in best.items():
        release = picked["release"]
        tag = release["tag_name"]
        files = []
        for asset in release.get("assets") or []:
            entry = build_file_entry(asset)
            if entry is not None:
                files.append(entry)
        entries.append(
            {
                "version": version,
                "stable": is_stable(version),
                "release_url": f"https://github.com/{repo}/releases/tag/{tag}",
                "files": files,
            }
        )

    entries.sort(key=lambda e: version_sort_key(e["version"]), reverse=True)

    out = json.dumps(entries, indent=2)
    with open("versions-manifest.json", "w", encoding="utf-8") as f:
        f.write(out)
    print(f"Wrote versions-manifest.json with {len(entries)} entries", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
