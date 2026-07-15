"""Create the API Gateway custom domain for the signing page, once the cert is issued.

Run after `check_domain` reports the certificate as issued. It creates the custom
domain, maps it to the API, and prints the DNS record that finally points
`sign.drorbrk.co.il` at it.

Deliberately a script rather than part of the SAM template: the domain depends on
a certificate that depends on a DNS record only Dror's registrar can add, so a
template that owned it would fail the whole stack every deploy until someone,
somewhere, clicked something. Infrastructure that waits on a human belongs outside
the thing that must deploy on demand.

Usage:
    python -m src.tools.setup_domain            # show the plan
    python -m src.tools.setup_domain --apply
"""

from __future__ import annotations

import argparse
import sys
from typing import Any, Optional

from ..lib import config

DEFAULT_DOMAIN = "sign.drorbrk.co.il"


def _client(service: str) -> Any:
    import boto3

    return boto3.client(
        service,
        region_name=config.get("AWS_REGION", "eu-central-1"),
        aws_access_key_id=config.get("AWS_ACCESS_KEY_ID"),
        aws_secret_access_key=config.get("AWS_SECRET_ACCESS_KEY"),
    )


def _cert_arn(domain: str) -> Optional[str]:
    acm = _client("acm")
    for s in acm.list_certificates(CertificateStatuses=["ISSUED"])["CertificateSummaryList"]:
        if s["DomainName"] == domain:
            return s["CertificateArn"]
    return None


def _api_id() -> str:
    """The HTTP API behind the signing page, found by its routes."""
    api = _client("apigatewayv2")
    for item in api.get_apis()["Items"]:
        routes = api.get_routes(ApiId=item["ApiId"])["Items"]
        if any(r["RouteKey"].endswith("/sign") for r in routes):
            return str(item["ApiId"])
    raise SystemExit("no API with a /sign route — is the stack deployed?")


def run(domain: str, apply: bool) -> bool:
    arn = _cert_arn(domain)
    if not arn:
        print(f"No ISSUED certificate for {domain}.")
        print("Run: python -m src.tools.check_domain")
        return False
    print(f"certificate: {arn}")

    api_id = _api_id()
    stage = config.get("STAGE", "dev")
    print(f"api:         {api_id} (stage {stage})")

    if not apply:
        print("\nWould create the custom domain and map it to the API.")
        print("Re-run with --apply.")
        return True

    gw = _client("apigatewayv2")
    try:
        got = gw.get_domain_name(DomainName=domain)
        print("custom domain already exists")
    except Exception:  # noqa: BLE001
        got = gw.create_domain_name(
            DomainName=domain,
            DomainNameConfigurations=[{
                "CertificateArn": arn,
                "EndpointType": "REGIONAL",
                "SecurityPolicy": "TLS_1_2",
            }],
        )
        print("custom domain created")

    target = got["DomainNameConfigurations"][0]["ApiGatewayDomainName"]

    mappings = gw.get_api_mappings(DomainName=domain).get("Items", [])
    if not mappings:
        # Empty path: the domain IS the base, so links are /sign rather than
        # /dev/sign — which is also why the form posts relatively.
        gw.create_api_mapping(DomainName=domain, ApiId=api_id, Stage=stage,
                              ApiMappingKey="")
        print(f"mapped {domain} -> api {api_id} stage {stage}")
    else:
        print("api mapping already exists")

    print("\nLast DNS record — add this at Wix:")
    print(f"  type:  CNAME")
    print(f"  host:  {domain.split('.')[0]}")
    print(f"  value: {target}")
    print(f"\nThen: SIGN_BASE_URL=https://{domain} in .env, redeploy, and links")
    print(f"become https://{domain}/sign?t=xxxxxxxx")
    return True


def main() -> None:
    parser = argparse.ArgumentParser(description="Create the signing custom domain")
    parser.add_argument("--domain", default=DEFAULT_DOMAIN)
    parser.add_argument("--apply", action="store_true", help="Actually create it.")
    args = parser.parse_args()
    config.load_dotenv()
    sys.exit(0 if run(args.domain, args.apply) else 1)


if __name__ == "__main__":
    main()
