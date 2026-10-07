# Security

## Reporting a vulnerability

Please report security issues privately through GitHub's private vulnerability reporting (the
repository's **Security** tab, then **Report a vulnerability**), not in a public issue. Include
what you found, how to reproduce it, and the impact you expect. We aim to acknowledge reports
within a week.

## What matters most

The broker (`broker/`) is the only server, and it holds the API keys:

- The OpenAI and TypeSafe keys stay on the server. The browser never sees them: the broker mints
  the GPT-Live session itself and signs a short-lived HMAC token that `/decide` requires.
- It enforces allowed origins, a per-session time cap, an idle hangup, per-IP daily limits and a
  daily dollar budget, and it never forwards upstream error bodies.
- It does not log or retain transcripts.

Reports about bypassing those limits, leaking keys or tokens, or spending someone else's budget
are the most useful. In the browser, transcripts are untrusted text; report any way to get markup
or script into the page through them.

## Supported versions

Only the latest `main` is supported.
