# Near-real-time trust layer

AI News Radar publishes complete static snapshots. It does not claim a live event stream.

## Public interfaces

- `data/snapshot-manifest.json` binds every UI data file to one deterministic `snapshot_id` and SHA-256 checksum.
- `data/news-overview.json` contains the lightweight AI News decision, pulse, coverage, and Top 3.
- `data/business-overview.json` contains the lightweight Business brief, action queue, clusters, and coverage.

The workflow runs `scripts/build_radar_overviews.py` after both collectors. The manifest is written last, so clients cannot discover a new snapshot before its versioned files exist.

## Trust states

Freshness and coverage are deliberately separate:

| Dimension | States |
| --- | --- |
| Freshness | `LIVE` <= 90 minutes; `DELAYED` <= 180 minutes; `STALE` > 180 minutes; `FALLBACK` when GitHub canonical is used |
| Coverage | `healthy` >= 80%; `limited` >= 50%; `critical` < 50% |

The browser retries the portal manifest, validates overview versions against it, then falls back to GitHub Raw. A previously validated overview is retained locally if neither surface can supply one complete version.

## UI and performance contract

- The two static URLs share a 224px desktop Split View and a compact mobile channel switch.
- Theme defaults to the operating system and supports persistent `auto`, `light`, and `dark` modes.
- Initial AI News rendering uses only the manifest and overview. The complete signal stream loads after explicit search, scroll intent, or the load command.
- Quick Look supports keyboard focus, `Esc`, and backdrop close.
- No external fonts, animation libraries, UI frameworks, or remote icon packages are required.

Rebuild the public interfaces with:

```bash
python3 scripts/build_radar_overviews.py --output-dir data
```
