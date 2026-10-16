"""Check Revenue Engine configuration and local DB; optional read-only provider checks."""
from __future__ import annotations
import argparse
import sys
import requests
from app.config import settings
from app.database import connect, init_db


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--online", action="store_true", help="Call read-only provider endpoints; never sends email")
    args = parser.parse_args()
    errors = []
    print("REVENUE ENGINE PREFLIGHT")
    print(f"Environment: {settings.environment}")
    print(f"Database path: {settings.db_path}")
    print(f"Landing page: {settings.landing_page_url}")
    print(f"Email provider: {settings.email_provider}")

    if settings.email_provider == "brevo":
        if not settings.brevo_api_key:
            errors.append("BREVO_API_KEY is missing")
        if not settings.brevo_sender:
            errors.append("BREVO_SENDER is missing")
        print("Brevo key configured:", bool(settings.brevo_api_key))
        print("Brevo sender configured:", bool(settings.brevo_sender))
    elif settings.email_provider == "sendgrid":
        if not settings.sendgrid_api_key:
            errors.append("SENDGRID_API_KEY is missing")
        if not settings.sendgrid_sender:
            errors.append("SENDGRID_SENDER is missing")
        print("SendGrid key configured:", bool(settings.sendgrid_api_key))
        print("SendGrid sender configured:", bool(settings.sendgrid_sender))

    if settings.environment == "production" and not settings.flask_secret_key:
        errors.append("FLASK_SECRET_KEY is required in production")
    try:
        init_db()
        with connect() as conn:
            integrity = conn.execute("PRAGMA integrity_check").fetchone()[0]
            leads = conn.execute("SELECT COUNT(*) FROM leads").fetchone()[0]
            outreach = conn.execute("SELECT COUNT(*) FROM outreach").fetchone()[0]
        print("Database integrity:", integrity)
        print("Lead records:", leads)
        print("Outreach records:", outreach)
        if integrity != "ok":
            errors.append("Database integrity check failed")
    except Exception as exc:
        errors.append(f"Database check failed: {type(exc).__name__}")

    if args.online:
        try:
            if settings.email_provider == "brevo" and settings.brevo_api_key:
                headers = {"api-key": settings.brevo_api_key, "accept": "application/json"}
                account = requests.get("https://api.brevo.com/v3/account", headers=headers, timeout=15)
                print("Brevo account API status:", account.status_code)
                if account.status_code != 200:
                    errors.append(f"Brevo account API returned HTTP {account.status_code}")
                elif settings.brevo_sender:
                    senders = requests.get("https://api.brevo.com/v3/senders", headers=headers, params={"limit": 50, "offset": 0}, timeout=15)
                    print("Brevo senders API status:", senders.status_code)
                    found = False
                    if senders.status_code == 200:
                        found = any(item.get("email", "").lower() == settings.brevo_sender.lower() and item.get("active") for item in senders.json().get("senders", []))
                    if senders.status_code != 200 or not found:
                        errors.append("Brevo sender is not listed as active")
                    print("Configured Brevo sender active:", found)
            elif settings.email_provider == "sendgrid" and settings.sendgrid_api_key:
                headers = {"Authorization": f"Bearer {settings.sendgrid_api_key}"}
                scopes = requests.get("https://api.sendgrid.com/v3/scopes", headers=headers, timeout=15)
                print("SendGrid scopes API status:", scopes.status_code)
                if scopes.status_code != 200 or "mail.send" not in scopes.json().get("scopes", []):
                    errors.append("SendGrid key lacks readable mail.send scope or scopes endpoint failed")
                if settings.sendgrid_sender:
                    verified = requests.get("https://api.sendgrid.com/v3/verified_senders", headers=headers, timeout=15)
                    found = verified.status_code == 200 and any(item.get("from_email", "").lower() == settings.sendgrid_sender.lower() and item.get("verified") for item in verified.json().get("results", []))
                    print("Configured SendGrid sender verified:", found)
                    if not found:
                        errors.append("Configured SendGrid sender is not verified")
        except requests.RequestException as exc:
            errors.append(f"Provider read-only check failed: {type(exc).__name__}")

    if errors:
        print("\nPREFLIGHT FAILED:")
        for error in errors:
            print("-", error)
        raise SystemExit(1)
    print("\nPREFLIGHT PASSED. No email was sent.")

if __name__ == "__main__":
    main()
