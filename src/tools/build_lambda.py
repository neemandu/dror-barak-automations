"""Build the Lambda package without Docker — the input ``deploy_stack`` expects.

``sam build --use-container`` needs Docker, which a Windows laptop often doesn't
have. It only does two things for this stack, and both can be done without it:

1. **Copy the code** — every function's ``CodeUri`` is the repo root. Copied from
   a *clean git export* of ``HEAD``, never the working folder: the working folder
   holds ``.env``, ``logs/`` and client documents, and whatever is in the build
   ships to AWS. Uncommitted changes are therefore not deployed — commit first.
2. **Install ``requirements.txt`` for Lambda** — Linux on arm64, Python 3.12.
   ``pip --platform manylinux2014_aarch64 --only-binary=:all:`` fetches those
   wheels on any OS; it is AWS's documented route for cross-platform packages.

The result is ``.aws-sam/build``: one ``Code`` folder and a ``template.yaml`` whose
functions all point at it (identical code, so ``sam package`` uploads it once).

Usage:
    python -m src.tools.build_lambda
    python -m src.tools.deploy_stack --stack dror-automations-dev --env-file .env
"""

from __future__ import annotations

import argparse
import io
import re
import shutil
import subprocess
import sys
import tarfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
TEMPLATE = ROOT / "infra" / "template.yaml"
CODE_DIR = "Code"
PLATFORM = "manylinux2014_aarch64"  # template: Architectures [arm64]
PYTHON = "3.12"  # template: Runtime python3.12


def export_head(dest: Path) -> str:
    """Write the committed tree at HEAD into ``dest``; return the commit id."""
    commit = subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=ROOT,
                            check=True, capture_output=True, text=True).stdout.strip()
    archive = subprocess.run(["git", "archive", "--format=tar", "HEAD"], cwd=ROOT,
                             check=True, capture_output=True).stdout
    with tarfile.open(fileobj=io.BytesIO(archive)) as tar:
        tar.extractall(dest, filter="data")
    return commit


def install_requirements(dest: Path) -> None:
    subprocess.run(
        [sys.executable, "-m", "pip", "install", "--quiet",
         "--requirement", str(dest / "requirements.txt"),
         "--target", str(dest),
         "--platform", PLATFORM, "--implementation", "cp",
         "--python-version", PYTHON, "--only-binary=:all:",
         "--upgrade"],
        check=True,
    )


def built_template(source: str) -> str:
    """The template with every ``CodeUri: ../`` pointed at the shared build dir."""
    out, count = re.subn(r"(?m)^(\s+CodeUri:\s*)\.\./\s*$", rf"\g<1>{CODE_DIR}", source)
    if not count:
        raise SystemExit("no `CodeUri: ../` found in infra/template.yaml; the "
                         "template changed shape; update build_lambda.")
    return out


def dirty() -> bool:
    status = subprocess.run(["git", "status", "--porcelain", "--untracked-files=no"],
                            cwd=ROOT, check=True, capture_output=True, text=True).stdout
    return bool(status.strip())


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--build-dir", default=ROOT / ".aws-sam" / "build", type=Path)
    args = parser.parse_args()

    if dirty():
        print("note: uncommitted changes are NOT in this build (it exports HEAD).")

    build = args.build_dir
    if build.exists():
        shutil.rmtree(build)
    code = build / CODE_DIR
    code.mkdir(parents=True)

    commit = export_head(code)
    print(f"exported {commit} -> {code}")
    install_requirements(code)
    print(f"installed requirements for {PLATFORM} / Python {PYTHON}")
    (build / "template.yaml").write_text(
        built_template(TEMPLATE.read_text(encoding="utf-8")), encoding="utf-8")
    print(f"wrote {build / 'template.yaml'}")


if __name__ == "__main__":
    main()
