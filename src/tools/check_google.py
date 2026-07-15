"""Check the Google service account is set up and can act as Dror.

Google's setup has several steps in two different consoles, and a mistake in any
one surfaces as the same unhelpful "unauthorized_client". This tests each link in
the chain and says which one is broken.

Read-only apart from `--test-drive`, which creates and deletes one folder.

Usage:
    python -m src.tools.check_google
    python -m src.tools.check_google --test-drive   # prove Drive writes work
"""

from __future__ import annotations

import argparse
import sys

from ..lib import config, google_auth
from ..lib.http import request as http_request

OK = "  [ok]  "
BAD = "  [!!]  "


def check() -> bool:
    ok = True

    print("1. the key")
    try:
        info = google_auth._key_info()
        print(f"{OK}service account: {info.get('client_email')}")
        print(f"{OK}client id:       {info.get('client_id')}   <- used in step 5")
        print(f"{OK}project:         {info.get('project_id')}")
    except google_auth.GoogleAuthError as exc:
        print(f"{BAD}{exc}")
        return False

    print("\n2. who it acts as")
    subject = config.get("GOOGLE_IMPERSONATE_SUBJECT")
    if subject:
        print(f"{OK}impersonating: {subject}")
    else:
        print(f"{BAD}GOOGLE_IMPERSONATE_SUBJECT not set — see docs/GOOGLE_SETUP.md step 6")
        return False

    print("\n3. minting a token (this is where delegation is proven)")
    try:
        token = google_auth.access_token(force_refresh=True)
        print(f"{OK}got a token ({len(token)} chars)")
    except google_auth.GoogleAuthError as exc:
        print(f"{BAD}{exc}")
        return False

    headers = {"Authorization": f"Bearer {token}"}

    print("\n4. the scopes actually granted")
    try:
        info = http_request(
            "GET", "https://www.googleapis.com/oauth2/v3/tokeninfo",
            params={"access_token": token},
        ).json()
        granted = set(str(info.get("scope", "")).split())
        for scope in google_auth.SCOPES:
            if scope in granted:
                print(f"{OK}{scope.rsplit('/', 1)[-1]}")
            else:
                print(f"{BAD}{scope}  NOT granted — add it in step 5")
                ok = False
    except Exception as exc:  # noqa: BLE001
        print(f"{BAD}could not read token info: {exc}")
        ok = False

    print("\n5. the APIs respond as Dror")
    probes = [
        ("Drive", "https://www.googleapis.com/drive/v3/about", {"fields": "user"}),
        # Probe the contact *list*, not people/me: reading your own profile needs a
        # userinfo.profile scope we deliberately don't ask for, so people/me 403s
        # on a perfectly good setup. connections.list uses the `contacts` scope,
        # which is what create_contact actually needs.
        ("People (Contacts)", "https://people.googleapis.com/v1/people/me/connections",
         {"personFields": "names", "pageSize": "1"}),
    ]
    for label, url, params in probes:
        try:
            resp = http_request("GET", url, headers=headers, params=params)
            body = resp.json()
            who = (body.get("user") or {}).get("emailAddress") or subject
            print(f"{OK}{label}: responding as {who}")
        except Exception as exc:  # noqa: BLE001
            print(f"{BAD}{label}: {str(exc)[:160]}")
            ok = False

    print("\n6. the clients folder")
    parent = config.get("DRIVE_CLIENTS_PARENT_ID")
    if not parent:
        print("  [--]  DRIVE_CLIENTS_PARENT_ID not set — onboarding has nowhere to "
              "create client folders")
    else:
        try:
            body = http_request(
                "GET", f"https://www.googleapis.com/drive/v3/files/{parent}",
                headers=headers,
                params={"fields": "id,name,mimeType", "supportsAllDrives": "true"},
            ).json()
            print(f"{OK}{body.get('name')!r} is reachable")
        except Exception as exc:  # noqa: BLE001
            print(f"{BAD}cannot read folder {parent}: {str(exc)[:120]}")
            ok = False

    print("\n" + ("READY — Google is set up." if ok else
                  "NOT READY — see the [!!] lines and docs/GOOGLE_SETUP.md."))
    return ok


def test_drive() -> bool:
    """Create and delete a folder — proves write access, leaves nothing behind."""
    parent = config.get("DRIVE_CLIENTS_PARENT_ID")
    if not parent:
        print("DRIVE_CLIENTS_PARENT_ID not set; nothing to test against.")
        return False
    headers = {"Authorization": f"Bearer {google_auth.access_token()}"}
    print("creating a test folder...")
    created = http_request(
        "POST", "https://www.googleapis.com/drive/v3/files", headers=headers,
        params={"fields": "id,webViewLink,owners", "supportsAllDrives": "true"},
        json={"name": "בדיקה — למחיקה", "mimeType": "application/vnd.google-apps.folder",
              "parents": [parent]},
    ).json()
    print(f"{OK}created: {created.get('webViewLink')}")
    owners = [o.get("emailAddress") for o in created.get("owners") or []]
    if owners:
        # If the service account owns it, Dror will not see it in his Drive.
        print(f"{OK}owned by: {owners}")
    print("deleting it...")
    http_request("DELETE", f"https://www.googleapis.com/drive/v3/files/{created['id']}",
                 headers=headers, params={"supportsAllDrives": "true"})
    print(f"{OK}deleted — nothing left behind")
    return True


def main() -> None:
    parser = argparse.ArgumentParser(description="Check the Google service account")
    parser.add_argument("--test-drive", action="store_true",
                        help="Create and delete a folder to prove write access.")
    args = parser.parse_args()
    config.load_dotenv()
    if args.test_drive:
        sys.exit(0 if test_drive() else 1)
    sys.exit(0 if check() else 1)


if __name__ == "__main__":
    main()
