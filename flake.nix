{
  description = "Demand Radar development environment";

  inputs = {
    nixpkgs.url = "github:NixOS/nixpkgs/nixos-unstable";

    # 007 is the default agent runner backend (`o7 invoke`) — see
    # docs/o7-invoke.md. Pinning it here as a flake input replaces "build the
    # sibling 007 repo by hand and put it on PATH" with a reproducible,
    # updatable dependency: the exact 007 revision is recorded in
    # flake.lock, and bumping it is one command —
    # `nix flake lock --update-input o7` — not a manual `git pull` + rebuild
    # in a sibling checkout. Deliberately NOT setting
    # `inputs.nixpkgs.follows = "nixpkgs"` here: 007 builds against its own
    # nixpkgs pin (currently nixos-25.05, via crane), and forcing it onto
    # this flake's nixpkgs risks a broken rebuild for no benefit — this
    # mirrors 007's own stated reasoning for not `follows`-ing its
    # `codex-cli` input (see 007/flake.nix). 007 is a private repo, so nix
    # needs a GitHub token with repo read access configured in
    # `access-tokens` before this input can be fetched — see
    # docs/nix-dev-shell.md for setup.
    o7.url = "github:PhysShell/007";
  };

  # NOTE: this flake edit (the `o7` input, the devshell package, and the
  # shellHook line below) was authored in an environment without nix
  # installed and has not been `nix flake check`- or `nix develop`-validated.
  # The first person to enter this shell after the edit should expect a
  # possible first-build of 007 via crane (it is not cached anywhere yet),
  # and should not be surprised if something here needs a small fix — see
  # docs/nix-dev-shell.md.
  outputs =
    { nixpkgs, o7, ... }:
    let
      system = "x86_64-linux";
      pkgs = import nixpkgs { inherit system; };
    in
    {
      devShells.${system}.default = pkgs.mkShellNoCC {
        packages = [
          pkgs.python312
          pkgs.uv
          pkgs.sqlite
          pkgs.git
          o7.packages.${system}.o7
        ];

        shellHook = ''
          echo "Demand Radar dev shell — python $(python3 --version), uv $(uv --version)"
          echo "Run: uv sync   (installs pinned deps from uv.lock into .venv)"
          echo "Then: uv run demand-radar --help"
          echo "Gate: ./scripts/check.sh"
          echo "o7 $(o7 --version 2>/dev/null || echo 'unavailable')"
        '';
      };
    };
}
