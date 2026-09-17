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


def _session(env: dict[str, str]) -> Any:
    import boto3

    return boto3.Session(
        region_name=env.get("AWS_REGION") or config.get("AWS_REGION", "eu-central-1"),
        aws_access_key_id=env.get("AWS_ACCESS_KEY_ID") or config.get("AWS_ACCESS_KEY_ID"),
        aws_secret_access_key=env.get("AWS_SECRET_ACCESS_KEY") or config.get("AWS_SECRET_ACCESS_KEY"),
    )


# Files that must be executable on Lambda. `sam build --use-container` copies the
# build out of its container through a tar stream that drops execute bits, so a
# package built that way carries Playwright's Node driver as a 122 MB file
# nobody may run, and the campaign report dies with "Permission denied".
EXECUTABLES = ("playwright/driver/node",)


def fix_executable_bits(build_dir: Path) -> list[Path]:
    """Restore the execute bit on known binaries in every function's build dir."""
    fixed = []
    for function_dir in (d for d in build_dir.iterdir() if d.is_dir()):
        for rel in EXECUTABLES:
            path = function_dir / rel
            if path.exists() and not (path.stat().st_mode & 0o100):
                path.chmod(path.stat().st_mode | 0o755)
                fixed.append(path)
    return fixed


def package(build_dir: Path, region: str) -> Path:
    """`sam package`: upload the built code (deduplicated by hash) and return the
    template whose CodeUris point at S3."""
    out = Path(tempfile.mkdtemp()) / "packaged.yaml"
    cmd = [sys.executable, "-m", "samcli", "package", "--template-file", str(build_dir / "template.yaml"),
           "--resolve-s3", "--region", region, "--output-template-file", str(out)]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        sys.stderr.write(proc.stdout[-2000:] + proc.stderr[-2000:])
        raise SystemExit("sam package failed")
    return out


def run(stack: str, env_file: Path, *, build_dir: Path, plan_only: bool = False,
        yes: bool = False) -> bool:
    env = read_env_file(env_file)
    session = _session(env)
    region = session.region_name
    cf = session.client("cloudformation")
    try:
        current = cf.describe_stacks(StackName=stack)["Stacks"][0]
        existing: Optional[set[str]] = {p["ParameterKey"] for p in current["Parameters"]}
        change_type = "UPDATE"
    except cf.exceptions.ClientError as exc:
        if "does not exist" not in str(exc):
            raise
        existing, change_type = None, "CREATE"

    fixed = fix_executable_bits(build_dir)
    if fixed:
        print(f"restored execute bit on {len(fixed)} file(s): {', '.join(str(f.relative_to(build_dir)) for f in fixed)}")
    packaged = package(build_dir, region)
    params = template_parameters(packaged)
    parameters, lines = plan_parameters(params, env, existing=existing)
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
    if plan_only:
        print("\n--plan: change set left for review:", cs["Id"])
        return True
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
    args = parser.parse_args()
    ok = run(args.stack, args.env_file, build_dir=args.build_dir, plan_only=args.plan, yes=args.yes)
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
