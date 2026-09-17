"""Build and publish the Chromium Lambda layer for the campaign report.

Chromium does not fit in the function package — the zip is already 62 MB and
Lambda unzips to a 250 MB cap — so it rides as a **layer**: sparticuz/chromium's
arm64 pack (a Lambda-tuned ``headless_shell``, the AL2023 shared libraries it
needs, SwiftShader, and a ``fonts.conf``), plus DejaVu Sans, because the pack's
Open Sans has no Hebrew and the report is Hebrew. ``src/lib/pdf_chromium.py``
unpacks it into ``/tmp`` at cold start.

The pack version should track the Chromium that Playwright expects (Playwright
1.63 ↔ Chromium 153); a mismatch usually still prints PDFs, but is untested.

Usage:
    python -m src.tools.publish_chromium_layer                   # v153.0.0
    python -m src.tools.publish_chromium_layer --version 154.0.0

Prints the layer version ARN. Put it in .env as CHROMIUM_LAYER_ARN and push it:
    python -m src.tools.push_stack_params ChromiumLayerArn
"""

from __future__ import annotations

import argparse
import io
import sys
import tarfile
import tempfile
import zipfile
from pathlib import Path
from typing import Any

import requests

from ..lib import config

DEFAULT_VERSION = "153.0.0"
LAYER_NAME = "dror-chromium-arm64"
PACK_URL = "https://github.com/Sparticuz/chromium/releases/download/v{v}/chromium-v{v}-pack.arm64.tar"
FONTS = Path(__file__).resolve().parents[2] / "templates" / "assets" / "fonts"
PACK_FILES = ("al2023.tar.br", "chromium.br", "fonts.tar.br", "swiftshader.tar.br")


def _session() -> Any:
    import boto3

    return boto3.Session(
        region_name=config.get("AWS_REGION", "eu-central-1"),
        aws_access_key_id=config.get("AWS_ACCESS_KEY_ID"),
        aws_secret_access_key=config.get("AWS_SECRET_ACCESS_KEY"),
    )


def _sam_bucket(session: Any) -> str:
    """The bucket `sam deploy --resolve-s3` manages; the layer zip lives beside the code."""
    cf = session.client("cloudformation")
    for o in cf.describe_stacks(StackName="aws-sam-cli-managed-default")["Stacks"][0]["Outputs"]:
        if o["OutputKey"] == "SourceBucket":
            return str(o["OutputValue"])
    raise SystemExit("no aws-sam-cli-managed-default stack — run one sam deploy first")


def build_zip(pack_tar: Path, out: Path) -> None:
    """Layer layout: /opt/chromium/<pack files> and /opt/fonts/<Hebrew-capable fonts>."""
    with tarfile.open(pack_tar) as tar, zipfile.ZipFile(out, "w", zipfile.ZIP_STORED) as z:
        # STORED, not DEFLATED: the payload is already Brotli-compressed, and Lambda
        # unzips layers itself — recompressing only slows both ends.
        names = tar.getnames()
        missing = [n for n in PACK_FILES if n not in names]
        if missing:
            raise SystemExit(f"pack is missing {missing}; got {names}")
        for name in PACK_FILES:
            member = tar.extractfile(name)
            assert member is not None
            z.writestr(f"chromium/{name}", member.read())
        for font in ("DejaVuSans.ttf", "DejaVuSans-Bold.ttf", "LICENSE-DejaVu.txt"):
            z.write(FONTS / font, f"fonts/{font}")


def run(version: str) -> str:
    session = _session()
    bucket = _sam_bucket(session)
    with tempfile.TemporaryDirectory() as tmp:
        pack = Path(tmp) / "pack.tar"
        url = PACK_URL.format(v=version)
        print(f"downloading {url}")
        with requests.get(url, stream=True, timeout=600) as r:
            r.raise_for_status()
            with open(pack, "wb") as fh:
                for chunk in r.iter_content(1 << 20):
                    fh.write(chunk)
        out = Path(tmp) / f"chromium-v{version}-arm64.zip"
        build_zip(pack, out)
        key = f"layers/chromium-v{version}-arm64.zip"
        print(f"uploading {out.stat().st_size / 1e6:.0f} MB to s3://{bucket}/{key}")
        session.client("s3").upload_file(str(out), bucket, key)

    lam = session.client("lambda")
    resp = lam.publish_layer_version(
        LayerName=LAYER_NAME,
        Description=f"sparticuz/chromium v{version} (arm64) + DejaVu Sans, for src/lib/pdf_chromium.py",
        Content={"S3Bucket": bucket, "S3Key": key},
        CompatibleRuntimes=["python3.12"],
        CompatibleArchitectures=["arm64"],
        LicenseInfo="Chromium: BSD-3-Clause; sparticuz/chromium: MIT; DejaVu: Bitstream Vera",
    )
    arn = str(resp["LayerVersionArn"])
    print(f"\nlayer published: {arn}")
    print("next: CHROMIUM_LAYER_ARN=<that> in .env, then "
          "python -m src.tools.push_stack_params ChromiumLayerArn")
    return arn


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--version", default=DEFAULT_VERSION, help="sparticuz/chromium release (default %(default)s)")
    args = parser.parse_args()
    config.load_dotenv()
    run(args.version)
    sys.exit(0)


if __name__ == "__main__":
    main()
