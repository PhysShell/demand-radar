# Nix dev shell and the `o7` binary

Demand Radar's default agent runner (`agents/o7_invoke.py::O7InvokeRunner`,
see [`docs/o7-invoke.md`](o7-invoke.md)) shells out to the `o7` binary from
the sibling [`007`](https://github.com/PhysShell/007) repo. That binary has
to come from somewhere before `--analyst claude --critic codex` (or any
non-`fake` runner) will work. There are two supported ways to get it.

## Option A: nix dev shell (pinned, reproducible)

`flake.nix` declares `007` as a flake input (`o7.url =
"github:PhysShell/007";`) and adds `o7.packages.${system}.o7` — 007's own
crane-built package output — to the dev shell's packages. Run:

```bash
nix develop
```

and `o7` is on `PATH` for the shell session, built from whatever revision of
007 is pinned in `flake.lock`. That pin is the point: the exact 007 commit
this repo was last known to work against is recorded in the lockfile, not
"whatever happened to be checked out in the sibling directory." To move to a
newer 007:

```bash
nix flake lock --update-input o7
```

which rewrites the `o7` entry in `flake.lock` to 007's current `main` and
rebuilds against it. `flake.nix` deliberately does not force
`inputs.nixpkgs.follows` onto 007's `nixpkgs` — 007 builds against its own
nixpkgs pin, and forcing it onto this repo's would risk a broken rebuild for
no real benefit. This mirrors 007's own reasoning for not `follows`-ing its
`codex-cli` input (see `007/flake.nix`).

**Private-repo token requirement:** `github:PhysShell/007` is a private
repository. Fetching it as a flake input requires nix to authenticate to
GitHub, which means a token with repo read access configured in your nix
config:

```
# /etc/nix/nix.conf or ~/.config/nix/nix.conf
access-tokens = github.com=<your-github-token>
```

Without this, `nix develop` (or any command that touches the `o7` input)
will fail to fetch it — that failure looks like a generic "unable to
download" error, not an explicit auth message, so if `nix develop` fails on
the `o7` input specifically, check `access-tokens` first.

## Option B: manual build (what CI/docs historically assumed)

Build 007 directly in a sibling checkout and put the resulting binary on
`PATH`:

```bash
cd ../007
cargo build
export PATH="$(pwd)/target/debug:$PATH"
```

This is the original, pre-flake-input way of getting `o7`, and it is still
fully supported — nothing in this repo requires the nix dev shell. It's
also the natural fallback if the nix route is unavailable (no nix installed,
no token configured, or the flake input has a problem — see below).

## Honest validation note

The `o7` flake input, its wiring into the dev shell's `packages`, and the
`shellHook` version line were authored in an environment without a nix
installation available, and have **not** been build-verified — no
`nix flake check`, no `nix develop` run against the real input. The Nix
syntax was written and reviewed carefully, but "carefully reviewed by a
human/model without nix" is not the same guarantee as "actually evaluated by
the Nix daemon." If `nix develop` fails on the `o7` input (auth, a stale
lock hash, an eval error, or anything else), fall back to
[Option B](#option-b-manual-build-what-cidocs-historically-assumed) above —
it does not depend on any of this flake wiring — and a fix PR against
`flake.nix` is welcome.

## Version compatibility

Whatever `o7` you end up with — pinned via the flake input or built manually
in a sibling checkout — has to satisfy `O7InvokeRunner`'s version handshake
with the `o7` binary, which currently expects the `0.1.x` line. See
[`docs/runner-contract.md`](runner-contract.md) for the actual contract
between this repo and the `o7` CLI surface it depends on.
