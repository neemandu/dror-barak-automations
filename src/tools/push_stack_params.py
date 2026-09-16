"""Push values from ``.env`` onto the deployed stack's parameters.

The stack's parameters *are* the production configuration, and they drift from
``.env``: the Meta token sat in ``.env`` for weeks while the stack's copy was
empty, and the monthly report died on the 1st, twice. A full ``sam deploy``
re-uploads 62 MB to change one value, and ``--parameter-overrides`` puts a
secret on the command line and in shell history.

This reads the named parameters from ``.env`` (values are never printed),
creates a parameter-only change set with every other parameter kept as is,
refuses to execute anything but Lambda environment changes, and verifies the
functions afterwards.

Usage:
    python -m src.tools.push_stack_params SmtpHost SmtpPort SmtpUser SmtpPassword DrorEmail
    python -m src.tools.push_stack_params SmooveWebhookToken --dry-run   # plan only, no AWS

Parameter names are the template's (``SmtpHost``); the ``.env`` key is found by
name (``SMTP_HOST``). Empty values are refused unless ``--allow-empty``, since
"blank out the SMTP password" is rarely what a typo meant.
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path
from typing import Any, Optional

from ..lib import config

DEFAULT_STACK = "dror-automations-dev"
TEMPLATE = Path(__file__).resolve().parents[2] / "infra" / "template.yaml"

# Change-set rows that a parameter change legitimately touches. Anything else
# means the template on the stack is not the one we think, and we stop.
_RIPPLE_TYPES = {"AWS::Lambda::Permission", "AWS::Events::Rule", "AWS::ApiGatewayV2::Api"}


def env_key_for(param: str, env_keys: list[str]) -> Optional[str]:
    """``SmtpHost`` -> ``SMTP_HOST``; ``ClickUpApiToken`` -> ``CLICKUP_API_TOKEN``.

    The template's env vars are the ``.env`` keys with the underscores taken
    out and CamelCase put in, so comparing with both stripped is exact.
    """
    want = param.lower()
    for key in env_keys:
        if key.replace("_", "").lower() == want:
            return key
    return None


def template_parameters() -> list[str]:
    """Parameter names declared in infra/template.yaml (for --dry-run)."""
    import yaml  # dev dependency; this tool never runs on Lambda

    class _Loader(yaml.SafeLoader):
        pass

    _Loader.add_multi_constructor("!", lambda loader, suffix, node: None)
    doc = yaml.load(TEMPLATE.read_text(encoding="utf-8"), Loader=_Loader)
    return sorted((doc.get("Parameters") or {}).keys())


def plan(params: list[str], known: list[str], *, allow_empty: bool) -> dict[str, tuple[str, str]]:
    """Resolve each parameter to ``(env_key, value)`` or fail with a clear message."""
    env_keys = [k for k in config_keys() if k]
    out: dict[str, tuple[str, str]] = {}
    for param in params:
        if param not in known:
            raise SystemExit(f"{param!r} is not a parameter of the stack. Known: {', '.join(known)}")
        key = env_key_for(param, env_keys)
        if not key:
            raise SystemExit(f"no .env key matches {param!r} (expected something like "
                             f"{_guess_key(param)}). Add it to .env first.")
        value = config.get(key) or ""
        if not value and not allow_empty:
            raise SystemExit(f"{key} is empty in .env. Fill it in, or pass --allow-empty "
                             f"to blank the stack's value on purpose.")
        out[param] = (key, value)
    return out


def config_keys() -> list[str]:
    import os

    return sorted(os.environ.keys())


def _guess_key(param: str) -> str:
    import re

    return re.sub(r"(?<!^)(?=[A-Z])", "_", param).upper()


def _session() -> Any:
    import boto3

    return boto3.Session(
        region_name=config.get("AWS_REGION", "eu-central-1"),
        aws_access_key_id=config.get("AWS_ACCESS_KEY_ID"),
        aws_secret_access_key=config.get("AWS_SECRET_ACCESS_KEY"),
    )


def _review(changes: list[dict[str, Any]]) -> tuple[bool, int]:
    """Only Lambda environment updates and their dependency ripple are allowed."""
    ok, env_functions = True, 0
    for change in changes:
        r = change["ResourceChange"]
        details = r.get("Details", [])
        attrs = sorted({f"{d['Target'].get('Attribute')}:{d['Target'].get('Name')}" for d in details})
        fn_env = r["ResourceType"] == "AWS::Lambda::Function" and attrs == ["Properties:Environment"]
        ripple = r["ResourceType"] in _RIPPLE_TYPES and all(d.get("Evaluation") == "Dynamic" for d in details)
        env_functions += fn_env
        if r["Action"] != "Modify" or not (fn_env or ripple):
            ok = False
            print(f"  [!!] unexpected change: {r['Action']} {r['LogicalResourceId']} {attrs}")
    return ok, env_functions


def run(params: list[str], *, stack: str = DEFAULT_STACK, dry_run: bool = False,
        allow_empty: bool = False) -> bool:
    if dry_run:
        resolved = plan(params, template_parameters(), allow_empty=allow_empty)
        for param, (key, value) in resolved.items():
            print(f"  {param:<24} <- {key} ({len(value)} chars)")
        print(f"\nDry run: would update {len(resolved)} parameter(s) on {stack}; everything else kept.")
        return True

    session = _session()
    cf = session.client("cloudformation")
    current = cf.describe_stacks(StackName=stack)["Stacks"][0]["Parameters"]
    resolved = plan(params, sorted(p["ParameterKey"] for p in current), allow_empty=allow_empty)
    for param, (key, value) in resolved.items():
        print(f"  {param:<24} <- {key} ({len(value)} chars)")

    parameters = [
        {"ParameterKey": p["ParameterKey"], "ParameterValue": resolved[p["ParameterKey"]][1]}
        if p["ParameterKey"] in resolved
        else {"ParameterKey": p["ParameterKey"], "UsePreviousValue": True}
        for p in current
    ]
    name = f"params-{int(time.time())}"
    cs = cf.create_change_set(StackName=stack, ChangeSetName=name, UsePreviousTemplate=True,
                              Parameters=parameters, Capabilities=["CAPABILITY_IAM"],
                              ChangeSetType="UPDATE")
    try:
        cf.get_waiter("change_set_create_complete").wait(
            StackName=stack, ChangeSetName=cs["Id"], WaiterConfig={"Delay": 5, "MaxAttempts": 60})
    except Exception:  # noqa: BLE001 - FAILED with "no updates" is the usual reason
        described = cf.describe_change_set(StackName=stack, ChangeSetName=cs["Id"])
        print(f"change set not created: {described.get('StatusReason', described['Status'])}")
        cf.delete_change_set(StackName=stack, ChangeSetName=cs["Id"])
        return False

    described = cf.describe_change_set(StackName=stack, ChangeSetName=cs["Id"])
    ok, env_functions = _review(described["Changes"])
    if not ok:
        print("Refusing to execute; the change set touched more than Lambda environments.")
        cf.delete_change_set(StackName=stack, ChangeSetName=cs["Id"])
        return False
    print(f"change set {name}: environment of {env_functions} function(s). Executing...")
    cf.execute_change_set(StackName=stack, ChangeSetName=cs["Id"])
    cf.get_waiter("stack_update_complete").wait(StackName=stack, WaiterConfig={"Delay": 10, "MaxAttempts": 60})
    status = cf.describe_stacks(StackName=stack)["Stacks"][0]["StackStatus"]
    print(f"stack: {status}")

    lam = session.client("lambda")
    functions = [r["PhysicalResourceId"] for r in cf.describe_stack_resources(StackName=stack)["StackResources"]
                 if r["ResourceType"] == "AWS::Lambda::Function"]
    all_good = status == "UPDATE_COMPLETE"
    for param, (key, value) in resolved.items():
        holders = []
        for fn in functions:
            env = (lam.get_function_configuration(FunctionName=fn).get("Environment") or {}).get("Variables", {})
            if key in env:
                holders.append((fn, env[key] == value))
        good = bool(holders) and all(match for _, match in holders)
        all_good &= good
        where = ", ".join(f"{fn}{'' if m else ' (MISMATCH)'}" for fn, m in holders) or "no function reads it"
        print(f"  [{'ok' if good else '!!'}] {key} on: {where}")
    return all_good


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("params", nargs="+", metavar="ParameterName")
    parser.add_argument("--stack", default=config.get("STACK_NAME", DEFAULT_STACK))
    parser.add_argument("--dry-run", action="store_true", help="Show the plan; touch nothing.")
    parser.add_argument("--allow-empty", action="store_true", help="Let an empty .env value blank the stack's.")
    args = parser.parse_args()
    config.load_dotenv()
    sys.exit(0 if run(args.params, stack=args.stack, dry_run=args.dry_run, allow_empty=args.allow_empty) else 1)


if __name__ == "__main__":
    main()
