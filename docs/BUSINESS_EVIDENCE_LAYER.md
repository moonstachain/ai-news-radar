# English-only Business Evidence Layer

This layer extends AI News Radar with a separate evidence feed for Yuanli IP.
It is not a generic business-news page. It collects public English sources and
compresses them into decision evidence for:

- AI business model innovation
- one-person company and tiny-team cases
- founder stories
- enterprise AI workflow adoption
- counter-signals around ROI, trust, and execution risk

## Data Contract

The update command writes six public JSON files under `data/`:

- `business-source-catalog.json`
- `business-latest-24h.json`
- `business-source-status.json`
- `business-stories-merged.json`
- `business-daily-brief.json`
- `business-case-bank.json`

The business layer remains separate from `latest-24h.json` and
`daily-brief.json` so the default AI news experience is not diluted.

## Source Policy

The default source set is English-only and public-only. It favors RSS/Atom and
stable public pages from business schools, consulting firms, VC/startup media,
OPC/bootstrapped founder communities, and AI commercialization sources.

Do not add default sources that require private cookies, paid inboxes, social
API credentials, LinkedIn sessions, or user-owned secrets. Those belong in a
future advanced layer.

## Scoring

Each signal is scored from seven components:

- source authority
- Yuanli relevance
- business-model value
- case concreteness
- OPC fit
- freshness
- counter-signal value

The UI shows the compressed brief, story clusters, source health, and OPC case
bank at `business.html`.
