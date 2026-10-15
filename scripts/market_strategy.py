"""Show international market hypotheses and validation gates."""
from __future__ import annotations
import argparse
from app.market_strategy import get_market, list_markets


def main() -> None:
    parser = argparse.ArgumentParser(description="Review target-country hypotheses before outreach.")
    parser.add_argument("--country", help="Country name; omit to list configured markets")
    args = parser.parse_args()
    profiles = [get_market(args.country)] if args.country else list_markets()
    for profile in profiles:
        print("=" * 68)
        print(f"{profile.country} | {profile.group} | priority={profile.priority}")
        print(f"Language hypothesis: {profile.language_hypothesis}")
        print(f"Initial hypothesis (not verified): {profile.initial_hypothesis}")
        print("Risks: " + ", ".join(profile.main_risks))
        print("Validate before outreach:")
        for check in profile.validation_checks:
            print(f"  - {check}")
    print("\nThese are planning hypotheses, not verified country-level market statistics.")

if __name__ == "__main__":
    main()
