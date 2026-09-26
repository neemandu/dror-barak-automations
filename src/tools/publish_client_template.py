"""Publish a client-facing guide from ``templates/client_docs`` into the templates folder.

The text lives in the repo (versioned, reviewed like code); the client gets it as a
branded Google Doc (:mod:`src.lib.branded_doc`) in Dror's templates folder
(``DRIVE_TEMPLATES_FOLDER_ID``). From there it is a template like any other: Dror
ticks it in the ``תבניות`` field on a client's task and it is copied into that
client's folder (:mod:`src.automations.client_templates`). The ``תבניות`` field
needs an option named exactly like the Doc, added once in ClickUp.

Usage:
    python -m src.tools.publish_client_template templates/client_docs/meta_ad_account.md \\
        --name "הוראות חיבור חשבון מודעות Meta"
    add --replace to trash the existing Doc of that name and publish afresh.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from ..lib import branded_doc, config, pdf, text_style
from ..lib.http import request

SUBTITLE = "מדריך קצר ללקוח"


def main() -> None:
    parser = argparse.ArgumentParser(description="Publish a client guide as a branded Google Doc template")
    parser.add_argument("source", help="Markdown file under templates/client_docs")
    parser.add_argument("--name", required=True, help="The Doc's name = the תבניות option")
    parser.add_argument("--replace", action="store_true", help="Trash an existing Doc of that name first")
    args = parser.parse_args()
    config.load_dotenv()

    folder = config.require("DRIVE_TEMPLATES_FOLDER_ID")
    markdown = text_style.humanize(Path(args.source).read_text(encoding="utf-8"))
    data = branded_doc.build(args.name, SUBTITLE, branded_doc.from_markdown(markdown))

    headers = pdf._headers()
    q = args.name.replace("'", "\\'")
    existing = request("GET", pdf.DRIVE, headers=headers, params={
        "q": f"'{folder}' in parents and name = '{q}' and trashed = false",
        "fields": "files(id,webViewLink)", "supportsAllDrives": "true",
        "includeItemsFromAllDrives": "true"}).json().get("files", [])
    if existing and not args.replace:
        print(f"Already in the templates folder: {existing[0].get('webViewLink')}\n"
              f"Use --replace to publish it again.")
        sys.exit(0)
    for f in existing:
        request("PATCH", f"{pdf.DRIVE}/{f['id']}", headers=headers,
                params={"supportsAllDrives": "true"}, json={"trashed": True})

    doc = pdf.file_to_google_doc(data, branded_doc.DOCX_TYPE, args.name, folder)
    print(f"Published: {doc.get('webViewLink')}")
    print(f"Add the option \"{args.name}\" to the תבניות field on the לקוחות list.")


if __name__ == "__main__":
    main()
