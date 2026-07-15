# Stack Overflow questions acquisition — 2026-07-15T22:15:40.901753+00:00

Product: `own-audit` · Run: `29454774085` · Request commit: `151e1b678cb53d9af91265914a183cdb0faaa995`

## Queries

- `wpf-memory-leak` (tagged ['wpf']): `memory leak` — fetched 30, eligible 28, selected 18, excluded by cap 10
- `csharp-memory-leak` (tagged ['c#']): `memory leak` — fetched 30, eligible 26, selected 17, excluded by cap 9
- `memory-profiler-root-cause` (tagged ['c#']): `memory profiler retained object` — fetched 5, eligible 5, selected 5, excluded by cap 0
- `event-handler-retention` (tagged ['c#']): `event handler memory leak` — fetched 30, eligible 21, selected 17, excluded by cap 4
- `idisposable-ownership` (tagged ['c#']): `IDisposable dispose memory leak` — fetched 19, eligible 17, selected 17, excluded by cap 0
- `propertychanged-performance` (tagged ['wpf']): `PropertyChanged performance` — fetched 11, eligible 9, selected 9, excluded by cap 0
- `dependencyproperty-retention` (tagged ['wpf']): `DependencyPropertyDescriptor AddValueChanged` — fetched 1, eligible 1, selected 1, excluded by cap 0
- `systemevents-retention` (tagged ['c#']): `SystemEvents memory leak` — fetched 1, eligible 0, selected 0, excluded by cap 0
- `large-wpf-performance` (tagged ['wpf']): `large WPF application performance` — fetched 11, eligible 11, selected 11, excluded by cap 0
- `architecture-dependency-cycle` (tagged ['c#']): `dependency cycle architecture` — fetched 5, eligible 5, selected 5, excluded by cap 0

## Counts

- fetched total: 143
- excluded total: 13 ({'missing_required_field': 10, 'deleted_owner': 3})
- duplicates (same question, multiple queries): 7
- eligible before cap: 123
- selection strategy: `round_robin_by_query_order_then_source_id_sort`
- over max_records cap (100): 23
- truncated bodies (> 8000 chars): 15
- **accepted (in evidence.jsonl): 100**
- unique authors: 98
- content licenses: {'CC BY-SA 4.0': 100}
- API quota: 288 / 300 remaining

## Files

- `data/evidence.jsonl` — RawEvidenceRecord JSONL, ready for `demand-radar ingest`
- `data/provenance.jsonl` — per-question owner/license/tags/scores/matched queries
- `data/manifest.json` — full counts, exclusions, hashes, quota
- `data/ATTRIBUTION.md` — Stack Overflow content-license notice
- evidence.jsonl sha256: `sha256:8232ab76183f23c22e7e9fd5e7815b57867bda28215782e3c20d34055ee35d9e`
- provenance.jsonl sha256: `sha256:7841baec3d3a53a8946404bc7dd225e928fb8830f5dcf972740883b414104710`
- manifest.json sha256: `sha256:d1b6df53774207210e4305b596c46d500812dcbe56e2b7c1e2e18cc80b0e31b8`
