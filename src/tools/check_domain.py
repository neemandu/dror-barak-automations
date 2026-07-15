"""Check the signing page's custom domain, step by step.

`sign.drorbrk.co.il` instead of a random AWS hostname. This is not cosmetic: a
client asked to sign a contract and type in their ח.פ at
`e3670c4ju8.execute-api.eu-central-1.amazonaws.com` is being asked to do exactly
what they are told never to do. A careful client hesitates, and spam filters agree
with them.

Three things have to line up, in order, and each fails differently:

  1. the certificate is validated  (a DNS record proving ownership)
  2. API Gateway has the custom domain, mapped to the API
  3. the domain's DNS points at API Gateway's target

This reports each and prints the exact record still needed. Read-only.

Usage:
    python -m src.tools.check_domain
"""

from __future__ import annotations

import argparse
import socket
import sys
from typing import Any, Optional

from ..lib import config

OK = "  [ok]  "
WAIT = "  [..]  "
BAD = "  [!!]  "

DEFAULT_DOMAIN = "sign.drorbrk.co.il"


def _client(service: str) -> Any:
    import boto3

    return boto3.client(
        service,
        region_name=config.get("AWS_REGION", "eu-central-1"),
        aws_access_key_id=config.get("AWS_ACCESS_KEY_ID"),
        aws_secret_access_key=config.get("AWS_SECRET_ACCESS_KEY"),
    )


def _cert(domain: str) -> Optional[dict[str, Any]]:
    acm = _client("acm")
    for summary in acm.list_certificates()["CertificateSummaryList"]:
        if summary["DomainName"] == domain:
            return acm.describe_certificate(
                CertificateArn=summary["CertificateArn"]
            )["Certificate"]
    return None


def check(domain: str) -> bool:
    print(f"\ndomain: {domain}")

    print("\n1. certificate")
    cert = _cert(domain)
    if not cert:
        print(f"{BAD}no certificate requested for {domain}")
        return False
    status = cert["Status"]
    if status == "ISSUED":
        print(f"{OK}issued")
    elif status == "PENDING_VALIDATION":
        record = (cert.get("DomainValidationOptions") or [{}])[0].get("ResourceRecord")
        print(f"{WAIT}waiting for the ownership record. Add this at Wix:")
        if record:
            host = record["Name"].replace(f".{domain.split('.', 1)[1]}.", "")
            print(f"         type:     CNAME")
            print(f"         host:     {host}")
            print(f"         value:    {record['Value']}")
        print("       Validation usually lands within minutes of the record going live.")
        return False
    else:
        print(f"{BAD}certificate is {status}")
        return False

    print("\n2. API Gateway custom domain")
    api = _client("apigatewayv2")
    try:
        got = api.get_domain_name(DomainName=domain)
        target = got["DomainNameConfigurations"][0]["ApiGatewayDomainName"]
        print(f"{OK}configured -> {target}")
    except Exception:  # noqa: BLE001
        print(f"{WAIT}not created yet — run: python -m src.tools.setup_domain")
        return False

    mappings = api.get_api_mappings(DomainName=domain).get("Items", [])
    if mappings:
        print(f"{OK}mapped to api {mappings[0]['ApiId']} stage {mappings[0]['Stage']}")
    else:
        print(f"{BAD}the domain exists but is not mapped to the API")
        return False

    print("\n3. the domain points at it")
    try:
        actual = socket.gethostbyname_ex(domain)
        aliases = [a for a in actual[1]] + [actual[0]]
        if any(target in a for a in aliases):
            print(f"{OK}{domain} resolves to API Gateway")
        else:
            print(f"{WAIT}{domain} resolves, but not to API Gateway yet.")
            print(f"         Add this at Wix — type: CNAME")
            print(f"         host:  {domain.split('.')[0]}")
            print(f"         value: {target}")
            return False
    except socket.gaierror:
        print(f"{WAIT}{domain} does not resolve yet. Add this at Wix:")
        print(f"         type:  CNAME")
        print(f"         host:  {domain.split('.')[0]}")
        print(f"         value: {target}")
        return False

    print(f"\nREADY — set SIGN_BASE_URL=https://{domain} and redeploy.")
    return True


def main() -> None:
    parser = argparse.ArgumentParser(description="Check the signing custom domain")
    parser.add_argument("--domain", default=DEFAULT_DOMAIN)
    args = parser.parse_args()
    config.load_dotenv()
    sys.exit(0 if check(args.domain) else 1)


if __name__ == "__main__":
    main()
