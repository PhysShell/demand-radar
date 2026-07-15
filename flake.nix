{
  description = "Demand Radar development environment";

  inputs = {
    nixpkgs.url = "github:NixOS/nixpkgs/nixos-unstable";
  };

  outputs =
    { nixpkgs, ... }:
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
        ];

        shellHook = ''
          echo "Demand Radar dev shell — python $(python3 --version), uv $(uv --version)"
          echo "Run: uv sync   (installs pinned deps from uv.lock into .venv)"
          echo "Then: uv run demand-radar --help"
          echo "Gate: ./scripts/check.sh"
        '';
      };
    };
}
