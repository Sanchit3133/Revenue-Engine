# Phase 1 implementation checklist

- Discovery from a source whose terms permit commercial use and retention.
- Read public business emails only from official websites and respect site access rules.
- Centralize recipient deduplication and suppression checks.
- Persist reminder due times in the shared PostgreSQL database.
- Cap follow-ups at two and stop reminders on reply, opt-out, bounce, or complaint.
- Keep live sending disabled until end-to-end tests and provider checks pass.
- Keep agreement, payment verification, and owner delivery approval as hard gates.
