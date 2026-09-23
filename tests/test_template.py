"""Sanity checks on infra/template.yaml that `sam validate --lint` does not make.

A function without `CodeUri: ../` is packaged from the template's own folder —
`infra/` — so it deploys fine, validates fine, and holds nothing but the template.
The first scheduled run then fails with "module not found", which for a daily
digest nobody is waiting on can go unnoticed for weeks. That is how the daily
email function was first built.
"""

from __future__ import annotations

import importlib
from pathlib import Path

import pytest
import yaml

TEMPLATE = Path(__file__).resolve().parents[1] / "infra" / "template.yaml"


class _CfnLoader(yaml.SafeLoader):
    """SafeLoader that tolerates CloudFormation's !Ref / !Sub / !GetAtt tags."""


_CfnLoader.add_multi_constructor("!", lambda loader, suffix, node: loader.construct_scalar(node)
                                 if isinstance(node, yaml.ScalarNode) else None)


@pytest.fixture(scope="module")
def functions() -> dict[str, dict]:
    doc = yaml.load(TEMPLATE.read_text(encoding="utf-8"), Loader=_CfnLoader)
    return {name: res["Properties"] for name, res in doc["Resources"].items()
            if res.get("Type") == "AWS::Serverless::Function"}


def test_every_function_ships_the_project_not_the_infra_folder(functions):
    for name, props in functions.items():
        assert props.get("CodeUri") == "../", f"{name} would be packaged without the code"


def test_every_handler_points_at_a_real_function(functions):
    for name, props in functions.items():
        module_name, _, attr = props["Handler"].rpartition(".")
        module = importlib.import_module(module_name)
        assert callable(getattr(module, attr, None)), f"{name}: {props['Handler']} does not exist"


def test_every_scheduled_function_can_write_the_run_log(functions):
    # A scheduled job's return value is not somewhere Dror looks; the run-log is.
    for name, props in functions.items():
        if not any("Schedule" in ev.get("Type", "") for ev in props.get("Events", {}).values()):
            continue
        tables = [p["DynamoDBCrudPolicy"]["TableName"] for p in props.get("Policies", [])
                  if isinstance(p, dict) and "DynamoDBCrudPolicy" in p]
        assert "RunLogTable" in tables, f"{name} cannot log what it did"


def test_no_deploy_can_delete_a_data_table():
    # A deploy from a template without the questionnaire table deleted it, with
    # every questionnaire and answer (2026-09-23). Retain drops a table from the
    # stack without touching the data.
    doc = yaml.load(TEMPLATE.read_text(encoding="utf-8"), Loader=_CfnLoader)
    tables = {k: v for k, v in doc["Resources"].items() if v["Type"] == "AWS::DynamoDB::Table"}
    assert tables
    for name, res in tables.items():
        assert res.get("DeletionPolicy") == "Retain", name
        assert res.get("UpdateReplacePolicy") == "Retain", name
