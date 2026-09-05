#!/usr/bin/env python
"""Re-authorize the Gmail channel and write a fresh gmail_token.json.

Run this whenever the email channel starts logging ``invalid_grant``: Google
expires refresh tokens for OAuth apps still in "Testing" publishing status
after 7 days, and revokes them when the account password changes.

    ./.venv/bin/python scripts/gmail_auth.py

Opens a browser for consent, then verifies the token by reading the mailbox
profile. On a headless box, pass --console to get a paste-the-code flow.
"""

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from google.oauth2.credentials import Credentials  # noqa: E402
from google_auth_oauthlib.flow import InstalledAppFlow  # noqa: E402
from googleapiclient.discovery import build  # noqa: E402

from channels.gmail_handler import SCOPES  # noqa: E402
from env_config import load_environment  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--console",
        action="store_true",
        help="print a URL to authorize manually instead of opening a browser",
    )
    args = parser.parse_args()

    load_environment(verbose=True)
    creds_file = os.getenv("GMAIL_CREDENTIALS_FILE", "gmail_credentials.json")
    token_file = os.getenv("GMAIL_TOKEN_FILE", "gmail_token.json")

    if not os.path.exists(creds_file):
        print(f"❌ {creds_file} not found.")
        print("   Download it from Google Cloud Console → APIs & Services →")
        print("   Credentials → OAuth 2.0 Client IDs → Desktop app → Download JSON")
        return 1

    flow = InstalledAppFlow.from_client_secrets_file(creds_file, SCOPES)
    if args.console:
        flow.redirect_uri = "urn:ietf:wg:oauth:2.0:oob"
        auth_url, _ = flow.authorization_url(prompt="consent", access_type="offline")
        print(f"\nOpen this URL, approve access, then paste the code back here:\n\n{auth_url}\n")
        flow.fetch_token(code=input("Authorization code: ").strip())
        credentials = flow.credentials
    else:
        print("\n🌐 Opening a browser for Google consent...")
        credentials = flow.run_local_server(port=0, prompt="consent", access_type="offline")

    with open(token_file, "w") as fh:
        fh.write(credentials.to_json())
    print(f"✅ Token written to {token_file}")

    # Prove the token actually works before declaring success.
    verified = Credentials.from_authorized_user_file(token_file, SCOPES)
    profile = build("gmail", "v1", credentials=verified).users().getProfile(userId="me").execute()
    print(f"✅ Authorized mailbox: {profile['emailAddress']} ({profile['messagesTotal']} messages)")

    if not credentials.refresh_token:
        print("\n⚠️  No refresh_token was issued — the worker will need re-auth on")
        print("   every restart. Revoke the app at https://myaccount.google.com/permissions")
        print("   and run this script again to force a fresh consent.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
