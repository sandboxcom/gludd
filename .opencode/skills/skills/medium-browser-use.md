---
{
  "name": "medium-browser-use",
  "description": "Live web and browser automation. Navigate URLs, click elements, fill forms, extract JS-rendered content, take screenshots. Turns agent into end-to-end QA and research operator. Source: unicodeveloper/medium-2026.",
  "tags": [
    "browser",
    "automation",
    "qa",
    "testing",
    "scraping",
    "medium-2026"
  ],
  "category": "automation"
}
---

# Browser Use

Give the agent control of a headless browser for live web interaction.

## Capabilities

- Navigate to URLs and follow links
- Click elements (buttons, tabs, accordions)
- Fill forms (login, signup, search)
- Extract content from JavaScript-rendered pages (SPA, dynamic content)
- Take screenshots for visual verification
- Handle multi-step workflows (signup flow, checkout, onboarding)

## When to Use

- End-to-end QA: verify deployed features work in a real browser
- Research: find and extract information from live web pages
- Automation: fill repetitive forms, extract data from dashboards
- Validation: check that staging/production matches expectations

## Workflow Pattern

1. Open the target URL
2. Wait for page to load (handle SPA rendering)
3. Interact with elements (click, type, scroll)
4. Extract content or take screenshots
5. Report findings with evidence

## Example Workflows

**Signup flow validation:**
Open staging URL -> fill test email -> fill password -> click submit ->
follow verification link -> screenshot dashboard -> report success/failure

**Research task:**
Search query -> open top results -> extract key information ->
synthesize findings with source URLs

## Install reference

Original: `npx skills add https://github.com/browser-use/browser-use --skill browser-use`
