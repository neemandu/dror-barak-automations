"""Deploy a stack with every parameter read from an env file — no secret on any
command line, and nothing silently reset to a template default.

``sam deploy`` needs either ``--guided`` (interactive) or ``--parameter-overrides``
(secrets in shell history); a plain ``sam deploy`` keeps previous values but can
only *update*. This does both create and update from one source of truth:

* UPDATE: parameters present and non-empty in the env file are set; every other
  parameter keeps its current value on the stack.
* CREATE: parameters present in the env file are set; the rest take the
  template default, and a required parameter with no default fails loudly.

Values are matched by name (``SmtpHost`` ↔ ``SMTP_HOST``) exactly as
:mod:`src.tools.push_stack_params` does. The code itself is uploaded by
``sam package`` from ``.aws-sam/build`` — run ``sam build --use-container`` first.

Usage:
    python -m src.tools.deploy_stack --stack dror-automations-dev  --env-file .env
    python -m src.tools.deploy_stack --stack dror-automations-test --env-file .env.test
    add --plan to stop after printing the change set, --yes to execute without asking.

Two guards, since more than one person deploys this stack (2026-09-23: a deploy
from an old checkout replaced the day's code and deleted the questionnaire table):

* The stack records the commit it was deployed from (``GitCommit`` parameter,
  ``DeployedCommit`` output). A deploy from a checkout that does not contain that
  commit is refused: pull and merge first. ``--over-newer`` overrides.
* A change set that **removes** a resource is not executed without
  ``--allow-removals``: removing is how data and routes disappear.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any, Optional

from ..lib import config
from .push_stack_params import env_key_for

ROOT = Path(__file__).resolve().parents[2]


def read_env_file(path: Path) -> dict[str, str]:
    """The same tiny KEY=VALUE grammar as config.load_dotenv, into a dict."""
    out: dict[str, str] = {}
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        out[key.strip()] = value.strip().strip('"').strip("'")
    return out


def template_parameters(template_path: Path) -> dict[str, dict[str, Any]]:
    import yaml

    class _Loader(yaml.SafeLoader):
        pass

    _Loader.add_multi_constructor("!", lambda loader, suffix, node: None)
    doc = yaml.load(template_path.read_text(encoding="utf-8"), Loader=_Loader)
    return dict(doc.get("Parameters") or {})


def plan_parameters(params: dict[str, dict[str, Any]], env: dict[str, str], *,
                    existing: Optional[set[str]]) -> tuple[list[dict[str, Any]], list[str]]:
    """CloudFormation parameter list plus a printable, value-free summary."""
    keys = list(env.keys())
    out, lines = [], []
    for name, spec in params.items():
        key = env_key_for(name, keys)
        value = env.get(key or "", "")
        if value:
            out.append({"ParameterKey": name, "ParameterValue": value})
            lines.append(f"  {name:<26} <- {key} ({len(value)} chars)")
        elif existing is not None and name in existing:
            out.append({"ParameterKey": name, "UsePreviousValue": True})
            lines.append(f"  {name:<26} (kept)")
        elif "Default" in spec:
            out.append({"ParameterKey": name, "ParameterValue": str(spec["Default"])})
            lines.append(f"  {name:<26} = default {spec['Default']!r}")
        else:
            raise SystemExit(f"{name} has no value: add {key or name} to the env file "
                             f"(it has no default and the stack does not exist yet)")
    return out, lines


def head_commit() -> str:
    return subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT, check=True,
                          capture_output=True, text=True).stdout.strip()


def contains(commit: str, head: str = "HEAD") -> bool:
    """Whether ``head`` already has ``commit`` (False when it is not even known here)."""
    return subprocess.run(["git", "merge-base", "--is-ancestor", commit, head], cwd=ROOT,
                          capture_output=True).returncode == 0


def removals(changes: list[dict[str, Any]]) -> list[str]:
    """Resources a change set would delete from the stack."""
    return [c["ResourceChange"]["LogicalResourceId"] for c in changes
            if c.get("ResourceChange", {}).get("Action") == "Remove"]


def _session(env: dict[str, str]) -> Any:
    import boto3

    return boto3.Session(
        region_name=env.get("AWS_REGION") or config.get("AWS_REGION", "eu-central-1"),
        aws_access_key_id=env.get("AWS_ACCESS_KEY_ID") or config.get("AWS_ACCESS_KEY_ID"),
        aws_secret_access_key=env.get("AWS_SECRET_ACCESS_KEY") or config.get("AWS_SECRET_ACCESS_KEY"),
    )


# Things `CodeUri: ../` drags into every function that Lambda never runs. Dropped
# before packaging so that nothing under docs/ can ride along into Lambda,
# whatever the working folder held when the build ran (a July build shipped
# Dror's contract text and proposal this way).
PRUNE = (
    "tests", "docs", "examples", "infra", "client.yml", "requirements-dev.txt",
)
LAMBDA_UNZIPPED_LIMIT = 262_144_000
# The Chromium layer unzips to ~70 MB (publish_chromium_layer). Any function may
# get it attached, so every function's code must leave room for it.
LAYER_BUDGET = 72_000_000


def prune_build(build_dir: Path) -> int:
    """Remove non-runtime files from every function's build dir; returns bytes freed."""
    import shutil

    freed = 0
    for function_dir in (d for d in build_dir.iterdir() if d.is_dir()):
        targets = [function_dir / rel for rel in PRUNE] + list(function_dir.glob("*.md"))
        for path in targets:
            if path.is_dir():
                freed += sum(f.stat().st_size for f in path.rglob("*") if f.is_file())
                shutil.rmtree(path)
            elif path.exists():
                freed += path.stat().st_size
                path.unlink()
    return freed


def unzipped_size(path: Path) -> int:
    return sum(f.stat().st_size for f in path.rglob("*") if f.is_file())


def package(build_dir: Path, region: str, env: dict[str, str]) -> Path:
    """`sam package`: upload the built code (deduplicated by hash) and return the
    template whose CodeUris point at S3.

    The credentials come from the env file, like everything else here: the SAM
    subprocess would otherwise fall back to ~/.aws, which on a shared machine may
    be a different client's account entirely.
    """
    import os

    out = Path(tempfile.mkdtemp()) / "packaged.yaml"
    cmd = [sys.executable, "-m", "samcli", "package", "--template-file", str(build_dir / "template.yaml"),
           "--resolve-s3", "--region", region, "--output-template-file", str(out)]
    sub_env = {**os.environ, "SAM_CLI_TELEMETRY": "0", "AWS_DEFAULT_REGION": region}
    for key in ("AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY"):
        if env.get(key) or config.get(key):
            sub_env[key] = env.get(key) or str(config.get(key))
    sub_env.pop("AWS_PROFILE", None)
    proc = subprocess.run(cmd, capture_output=True, text=True, env=sub_env)
    if proc.returncode != 0:
        sys.stderr.write(proc.stdout[-2000:] + proc.stderr[-2000:])
        raise SystemExit("sam package failed")
    return out


def run(stack: str, env_file: Path, *, build_dir: Path, plan_only: bool = False,
        yes: bool = False, over_newer: bool = False, allow_removals: bool = False) -> bool:
    env = read_env_file(env_file)
    session = _session(env)
    region = session.region_name
    cf = session.client("cloudformation")
    head = head_commit()
    try:
        current = cf.describe_stacks(StackName=stack)["Stacks"][0]
        existing: Optional[set[str]] = {p["ParameterKey"] for p in current["Parameters"]}
        change_type = "UPDATE"
    except cf.exceptions.ClientError as exc:
        if "does not exist" not in str(exc):
            raise
        current, existing, change_type = {}, None, "CREATE"

    live = next((o["OutputValue"] for o in current.get("Outputs", [])
                 if o["OutputKey"] == "DeployedCommit"), "")
    if live and live != "unknown" and not contains(live):
        message = (f"{stack} runs commit {live[:10]}, which this checkout ({head[:10]}) does not "
                   f"contain: someone deployed newer code. git pull (and merge) first.")
        if not over_newer:
            raise SystemExit(message + " (--over-newer to deploy anyway)")
        print("WARNING:", message)

    freed = prune_build(build_dir)
    if freed:
        print(f"pruned {freed / 1e6:.1f} MB of non-runtime files from the build")
    for function_dir in (d for d in build_dir.iterdir() if d.is_dir()):
        size = unzipped_size(function_dir)
        if size + LAYER_BUDGET > LAMBDA_UNZIPPED_LIMIT:
            # Found out the slow way once: a 60 MB upload, then a stack rollback.
            raise SystemExit(
                f"{function_dir.name} unzips to {size / 1e6:.1f} MB; with the Chromium layer "
                f"(~{LAYER_BUDGET / 1e6:.0f} MB) that exceeds Lambda's {LAMBDA_UNZIPPED_LIMIT / 1e6:.0f} MB. "
                f"Trim dependencies or extend PRUNE before deploying.")
    packaged = package(build_dir, region, env)
    params = template_parameters(packaged)
    parameters, lines = plan_parameters(params, env, existing=existing)
    if "GitCommit" in params:
        parameters = [p for p in parameters if p["ParameterKey"] != "GitCommit"]
        parameters.append({"ParameterKey": "GitCommit", "ParameterValue": head})
        lines = [ln for ln in lines if not ln.strip().startswith("GitCommit ")]
        lines.append(f"  {'GitCommit':<26} = {head[:10]} (this checkout)")
    print(f"{change_type} {stack} from {env_file} ({len(parameters)} parameters):")
    print("\n".join(lines))

    name = f"deploy-{int(time.time())}"
    cs = cf.create_change_set(
        StackName=stack, ChangeSetName=name, ChangeSetType=change_type,
        TemplateBody=packaged.read_text(encoding="utf-8"), Parameters=parameters,
        Capabilities=["CAPABILITY_IAM", "CAPABILITY_AUTO_EXPAND"],
    )
    try:
        cf.get_waiter("change_set_create_complete").wait(
            StackName=stack, ChangeSetName=cs["Id"], WaiterConfig={"Delay": 5, "MaxAttempts": 60})
    except Exception:  # noqa: BLE001
        d = cf.describe_change_set(StackName=stack, ChangeSetName=cs["Id"])
        print("change set not created:", d.get("StatusReason", d["Status"]))
        cf.delete_change_set(StackName=stack, ChangeSetName=cs["Id"])
        if change_type == "CREATE":
            cf.delete_stack(StackName=stack)  # a failed CREATE leaves a REVIEW_IN_PROGRESS shell
        return "didn't contain changes" in str(d.get("StatusReason", ""))

    d = cf.describe_change_set(StackName=stack, ChangeSetName=cs["Id"])
    print("\nchanges:")
    for c in d["Changes"]:
        r = c["ResourceChange"]
        attrs = sorted({f"{x['Target'].get('Name')}" for x in r.get("Details", []) if x["Target"].get("Name")})
        print(f"  {r['Action']:<7} {r['LogicalResourceId']:<42} {r['ResourceType']:<30} {attrs}")
    removed = removals(d["Changes"])
    if removed:
        print(f"\nThis change set REMOVES {', '.join(removed)} from the stack. If you did not "
              f"mean to delete them, your checkout is probably behind: git pull first.")
    if plan_only:
        print("\n--plan: change set left for review:", cs["Id"])
        return True
    if removed and not allow_removals:
        cf.delete_change_set(StackName=stack, ChangeSetName=cs["Id"])
        print("not executed (--allow-removals to remove them); change set deleted")
        return False
    if not yes:
        answer = input("\nExecute? [y/N] ").strip().lower()
        if answer != "y":
            cf.delete_change_set(StackName=stack, ChangeSetName=cs["Id"])
            print("not executed; change set deleted")
            return False

    cf.execute_change_set(StackName=stack, ChangeSetName=cs["Id"])
    print("executing...")
    waiter = "stack_create_complete" if change_type == "CREATE" else "stack_update_complete"
    cf.get_waiter(waiter).wait(StackName=stack, WaiterConfig={"Delay": 10, "MaxAttempts": 120})
    final = cf.describe_stacks(StackName=stack)["Stacks"][0]
    print("stack:", final["StackStatus"])
    for o in final.get("Outputs", []):
        print(f"  {o['OutputKey']:<22} {o['OutputValue']}")
    return final["StackStatus"].endswith("_COMPLETE") and "ROLLBACK" not in final["StackStatus"]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--stack", required=True)
    parser.add_argument("--env-file", default=".env", type=Path)
    parser.add_argument("--build-dir", default=Path(".aws-sam/build"), type=Path)
    parser.add_argument("--plan", action="store_true", help="Create the change set and stop.")
    parser.add_argument("--yes", action="store_true", help="Execute without asking.")
    parser.add_argument("--over-newer", action="store_true",
                        help="Deploy even though the stack runs a commit this checkout lacks.")
    parser.add_argument("--allow-removals", action="store_true",
                        help="Execute a change set that removes resources.")
    args = parser.parse_args()
    ok = run(args.stack, args.env_file, build_dir=args.build_dir, plan_only=args.plan, yes=args.yes,
             over_newer=args.over_newer, allow_removals=args.allow_removals)
    if ok and not args.plan:
        # Dror's guide in ClickUp follows what is now running (src/tools/sync_guide.py).
        from . import sync_guide

        config.load_dotenv()
        sync_guide.sync_if_configured()
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
