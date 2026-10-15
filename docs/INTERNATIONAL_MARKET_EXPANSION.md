# International market expansion: hypotheses, not assumptions

## Goal

Test target countries with low financial exposure and learn which reachable business segments can pay for a real, useful service. The country list below reflects the business owner's starting hypotheses; it is **not verified evidence** that AI adoption is low, competition is absent, English is widely used, or mobile money can pay an India-based supplier.

## Starting market groups

- **East Africa:** Ethiopia, Tanzania, Uganda, Rwanda, Zambia. English accessibility and mobile-money availability are hypotheses to validate by segment and by cross-border settlement route. Ethiopia requires particular care with language fit.
- **Francophone West Africa:** Senegal, Côte d'Ivoire, Cameroon. Test French-first outreach and landing-page copy where appropriate; Cameroon is linguistically mixed. Verify competition rather than assuming providers are few.
- **Central and Southeast Asia:** Uzbekistan, Cambodia, Laos, Mongolia, Nepal. Validate local language, discoverability, buyer maturity and cross-border payment feasibility before investing.
- **Islands and small markets:** Papua New Guinea, Fiji, Madagascar, Malawi. Test addressable lead volume and reachable decision-makers before committing resources.
- **Higher-risk hypotheses:** Tajikistan, Turkmenistan, Sierra Leone, Liberia, Burundi, Somalia. Payment access and trust must be proven before outreach volume increases.
- **Crowded-market comparison:** Nigeria, Bangladesh and Pakistan. The initial hypothesis is greater freelancer/agency competition; enter only with a differentiated niche supported by local evidence.

## Required validation gate for every country

Before treating a country as launch-ready, complete a market card with evidence and a date for each item:

1. **Payment:** identify a legal route for the customer to pay an India-based provider. Check the provider's country coverage, account eligibility, settlement currency, FX, fees, payout timing, refunds and chargebacks. Mobile money being popular locally does not prove it can settle to the operator's account. Do not promise a payment method until tested.
2. **Language:** inspect target-business pages and ask the decision-maker. Record the preferred language. Use human-reviewed local-language outreach and pages; do not machine-translate high-stakes terms or imply local fluency without review.
3. **Reachability:** manually verify whether the chosen niche appears on Google Maps, Facebook or WhatsApp and whether public business contact details are usable. Record the source URL and the date observed.
4. **Competition:** inspect at least 10 relevant local providers, compare the same service and segment, and record evidence. Do not equate few search results with no competition.
5. **Demand:** speak with at least 3 target businesses or run a small, compliant, opt-out-respecting discovery test. Record replies and reasons for rejection.
6. **Compliance and trust:** review applicable privacy, direct-marketing, consumer, tax and payment rules; make the sender identity clear, include an easy opt-out, suppress opt-outs immediately, and never misrepresent local presence.
7. **Unit economics:** estimate acquisition time/cost, service delivery time, payment fees, FX loss, refund exposure and realistic price acceptance. Continue only if the expected margin is viable.

## Suggested experiment sequence

1. Pick **one country and one city**, not all countries at once.
2. Select one niche (for example, language schools, training centres, clinics, or professional services) only after checking fit.
3. Manually review 20 candidate businesses; verify website/social presence, contact method, language and an observable conversion issue.
4. Contact only a small, compliant sample with a consultative question. No unsolicited price or proposal; establish the problem and requirements, then ask budget before discussing price. Honor no more than two follow-ups and all opt-outs.
5. Evaluate qualified replies, confirmed pain, budget disclosure, payment feasibility and delivery margin—not email volume alone.
6. Expand only after the first paid pilot and a documented post-mortem.

## Automation contract

The Revenue Engine may store a country, city, language hypothesis, source URLs and validation status, but must not infer payment capability, language proficiency, AI adoption, competition or willingness to pay from country alone. Unvalidated markets should remain `RESEARCH_REQUIRED`; no country-based automatic live sending.

Use `python -m scripts.market_strategy --country Uganda` to review the configured hypothesis and checks. Use `python -m scripts.market_strategy` to list all configured markets. The report is an operator checklist, not live market research.
