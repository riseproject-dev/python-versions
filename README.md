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

| CPython tag  | Release tag      | Asset                                                              |
| ------------ | ---------------- | ------------------------------------------------------------------ |
| `v3.12.13`   | `3.12.13`        | `python-3.12.13-linux-24.04-riscv64.tar.gz`                        |
| `v3.13.3`    | `3.13.3`         | `python-3.13.3-linux-24.04-riscv64.tar.gz` (+ `-freethreaded`)     |
| `v3.15.0a7`  | `3.15.0-alpha.7` | `python-3.15.0-alpha.7-linux-24.04-riscv64.tar.gz` (+ `-freethreaded`) |

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

## Manual dispatch

- Build one tag: trigger the **Build Python** workflow with `cpython_tag=v3.12.13` (optionally `freethreaded=true` for 3.13+).
- Backfill: trigger **Check CPython releases** with `max_dispatches` set to the number of tags you want to build (use `0` for a dry run).
