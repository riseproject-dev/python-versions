# python-versions (riscv64)

Prebuilt CPython binaries for `linux/riscv64`, published as GitHub Releases.

A scheduled workflow polls [`python/cpython`](https://github.com/python/cpython)
for new version tags (stable plus `a`/`b`/`rc` prereleases from 3.10 onward) and
dispatches a build on the GitHub-hosted `ubuntu-24.04-riscv` runner for every
tag that does not yet have a release here.

Tarball layout and naming follow
[`actions/python-versions`](https://github.com/actions/python-versions) so
downstream tooling (`actions/setup-python`, toolcache scripts) can consume
them unchanged. Free-threaded (`--disable-gil`) variants are published as
additional assets on the same release for CPython 3.13+.

## Release / asset naming

Release **tags** include the GitHub Actions `run_id` of the build so rebuilds
don't clobber older artefacts. Release **titles** and **notes** stay at the
plain normalised version.

| CPython tag  | Release title    | Release tag                      | Asset                                                                 |
| ------------ | ---------------- | -------------------------------- | --------------------------------------------------------------------- |
| `v3.12.13`   | `3.12.13`        | `3.12.13-<run_id>`               | `python-3.12.13-linux-24.04-riscv64.tar.gz`                           |
| `v3.13.3`    | `3.13.3`         | `3.13.3-<run_id>`                | `python-3.13.3-linux-24.04-riscv64.tar.gz` (+ `-freethreaded`)        |
| `v3.15.0a7`  | `3.15.0-alpha.7` | `3.15.0-alpha.7-<run_id>`        | `python-3.15.0-alpha.7-linux-24.04-riscv64.tar.gz` (+ `-freethreaded`) |

## Using a release

```sh
tar -xzf python-3.12.13-linux-24.04-riscv64.tar.gz
./python-3.12.13-linux-24.04-riscv64/bin/python3.12 --version
```

Or, to install into the GitHub Actions toolcache layout (`setup-python` compatible):

```sh
cd python-3.12.13-linux-24.04-riscv64
./setup.sh
```

## Versions manifest

`versions-manifest.json` at the repo root lists every published release in the
same schema as
[`actions/python-versions`](https://github.com/actions/python-versions/blob/main/versions-manifest.json),
mapping each `version` to its `release_url` and per-file `download_url`s.

The `Update versions-manifest.json` workflow regenerates it from the
authoritative release list whenever a release is published or deleted (and on
manual dispatch). Changes are force-pushed to the `auto/update-manifest` branch
as a single rolling PR against `main`.

## Manual dispatch

- Build one tag: trigger the **Build Python** workflow with `cpython_tag=v3.12.13` (optionally `freethreaded=true` for 3.13+).
- Backfill: trigger **Check CPython releases** with `max_dispatches` set to the number of tags you want to build (use `0` for a dry run).
