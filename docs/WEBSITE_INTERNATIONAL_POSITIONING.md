# Website update brief: international-ready service positioning

The static website source repository was not included in the Revenue Engine source archive. This document specifies the changes to apply to the website repository; it does not claim those website pages have already been changed.

## Positioning and page structure

Keep the main homepage and services anchor working. Add an **International Services** section with links to service pages for:

- Landing pages and business websites
- Lead capture and enquiry handling
- Automated first response and follow-up workflows
- Payment-page integration, subject to provider/country support
- Lightweight business automation

Use plain, outcome-based language: help a business capture enquiries, respond consistently, and track next steps. Do not claim guaranteed sales, guaranteed AI transformation, local offices, local clients, or country-specific payment support without evidence.

## International discovery section

Explain that service delivery is remote and that implementation is scoped after a short consultation. Invite the business owner to select their country, city, business type, preferred language and main enquiry problem. Do not ask for sensitive personal data at the initial lead stage.

Suggested form fields:

- Country and city
- Business type / industry
- Preferred contact language
- Website or public business page (optional)
- Where enquiries arrive (website, WhatsApp, Facebook, phone, other)
- The problem they want to solve
- Consent checkbox explaining how the details will be used to respond

Do not display or promise a local payment option until the operator has validated it for that country. The consultation remains problem-first; do not show pricing before the prospect's budget and requirements have been discussed.

## Localization

- English is the default, not an assumption about every prospect.
- Create French-first pages and outreach only after human review for French-speaking segments.
- Add additional languages only when there is a reviewed translation and a plan for replying in that language.
- Make the language selector explicit; preserve the visitor's selected language across the consultation flow.
- Keep privacy, consent, contact details and opt-out language understandable in each supported language.

## Trust and conversion elements

- Clearly identify Sanchit and the remote nature of the service.
- Show only verifiable portfolio samples and working demos.
- Explain the process: consultation → confirmed problem and requirements → budget discussion → written scope/agreement → payment and implementation.
- Include a contact route that is actually monitored and a transparent response-time expectation.
- Use HTTPS and test the form on mobile, slow connections and common browsers.

## Technical acceptance criteria

- Existing GitHub Pages DNS and routes remain intact.
- Every form submits to the real deployed backend over HTTPS; no fake success message.
- Consent is required before storing/contacting a lead; privacy notice and retention policy are linked.
- Country, city and language are passed to the Revenue Engine with validation state, not silently treated as verified market facts.
- Automated tests cover validation, consent, persistence, language selection and mobile layout.
- Payment options are shown only for routes verified to accept and settle payments for the operator.
