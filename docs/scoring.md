# Scoring

## Demand score is a ranking heuristic, not a market-size estimate and not a probability of commercial success.

It exists to sort opportunity candidates against each other within one
product's evidence set. It is not calibrated against any real outcome data,
has no unit, and its absolute magnitude means nothing outside this codebase.
Nothing in this system should ever present it as a probability, a
percentage chance of success, or a revenue estimate.

## Components and weights

`scoring/demand_score.py::compute_demand_score`, weights from
`config.py::ScoringWeights` (a product's YAML, or the class defaults —
never hidden in a prompt string):

```text
demand_score =
    frequency          * 1.0
  + recent_growth      * 1.5
  + pain               * 2.0
  + urgency            * 1.5
  + commercial_intent  * 2.5
  + commitment         * 4.0
  + product_fit        * 2.0
  + source_diversity   * 2.0
  + saturation         * (-1.5)
  + duplication        * (-2.0)
```

Every weight in `ScoringWeights` for `saturation`/`duplication` is stored
**already negative** (`-1.5`, `-2.0`), so the implementation is a single
uniform `sum(value * weight for ...)` — the sign lives in the weight, not
in a scattered minus sign at each call site. Result is clamped to `>= 0`
(`OpportunityCard.signals.demand_score` schema constraint).

Inputs, all normalized to `0..1` before weighting:

- **frequency** — canonical (non-duplicate) member count in the cluster,
  divided by `FREQUENCY_CAP = 10` and clamped to 1.0. A cap, not a
  probability: ten or more independent reports is "as frequent as this
  scoring cares to distinguish," not a hard ceiling on real demand.
- **recent_growth** — fraction of a cluster's canonical members published
  in the more recent half of the *whole accepted-evidence date range for
  this run* (not just this cluster's own range), so growth is measured
  against the same clock for every cluster.
- **pain, urgency, commercial_intent** — mean across the cluster's
  canonical members' `Classification.signals` (analyst-extracted, per
  evidence item).
- **commitment** — **max**, not mean, across canonical members. Commitment
  is meant to capture "did anyone show a concrete, near-term buying
  signal" (spec: "explicit commitment signal"), a genuinely bimodal
  question one strong data point should answer just as well as five, and
  averaging it against silent members would bury the one signal that
  actually matters most (it carries the highest weight, 4.0, for the same
  reason).
- **product_fit** — mean of `Classification.relevance.product_fit`.
- **source_diversity** — `source_families` (from `independence`, already
  computed over canonical members only), divided by
  `MAX_SOURCE_FAMILIES_FOR_DIVERSITY = 5` and clamped to 1.0.
- **saturation** — count of distinct `mentioned_tools` strings across the
  cluster's classifications, divided by `SATURATION_TOOL_CAP = 5` and
  clamped to 1.0. More named existing tools mentioned in the evidence
  itself is read as "this space already has competitors," a penalty. This
  is a proxy computed from evidence text, not a market survey.
- **duplication** — `scoring/independence.py::duplication_ratio`: fraction
  of the cluster's total (canonical + duplicate) evidence that turned out
  to be duplicate/repost, not independent.

## Why likes/stars/engagement have no weight at all — not just a low one

There is no engagement metric (upvotes, likes, star count, view count) in
this formula, on purpose, not because it was forgotten. `EvidenceItem` does
not even have a field for it. The reason: engagement measures how many
people clicked a button on someone else's post, which is not evidence of
*independent* demand — a single viral post can accumulate thousands of
upvotes from people who did nothing but react, which is categorically
different from thousands of people independently describing the same
problem in their own words. The mechanism that actually prevents a viral
post from dominating a score is `unique_authors` / `source_families`
(`scoring/independence.py`, always computed over **canonical** — i.e.
deduplicated — members only), not a discounted engagement term. See
`tests/integration/test_fixture_pipeline.py::test_viral_post_alone_cannot_reach_experiment_ready`
for the concrete case this guards against.

## Confidence

`scoring/demand_score.py::compute_confidence` — a separate 0..1 figure
(`OpportunityCard.signals.confidence`), not a component of `demand_score`.
It blends how far past the acceptance minimums the evidence sits
(`unique_authors`/`minimum_unique_authors`, `source_families`/
`minimum_source_families`, weighted 0.4/0.3) with the analyst's own mean
`relevance.confidence` across canonical members (weighted 0.3), halved if
the critic raised any fatal objection. Like `demand_score`, this reflects
how solid the *evidence* looks, not a probability of commercial success —
the same caveat applies.

## Independence and deduplication

`scoring/independence.py` and `ingest/deduplicate.py` implement spec
section 18's rules. Duplicates are found by three signals feeding one
union-find grouping: identical canonical URL, identical content hash, and
near-duplicate text (TF-IDF cosine over `ingest/similarity.py`, threshold
`0.5` — calibrated in `tests/unit/test_similarity.py` against real fixture
text: a genuine paraphrase-repost of the same post scores ~0.55-0.6 with
this measure, while two distinct posts on the same topic top out ~0.2).
`unique_authors` and `source_families` are **always** computed from
canonical (non-duplicate) members only, so a repost — even one crossposted
by a different account to a different platform — cannot inflate either
count, and a single author posting the same complaint twice collapses to
one independent signal without any author-matching special case: it falls
out of deduplication by construction.

## Deterministic acceptance thresholds

`config.py::AcceptanceThresholds`, real-mode defaults (never silently
lowered for fixtures — see `docs/decisions.log.md`):

```yaml
minimum_unique_authors: 3
minimum_source_families: 2
minimum_problem_evidence: 3
minimum_workaround_evidence: 1
```

`scoring/judge.py::judge_opportunity` is the sole place `OpportunityCard.status`
is written. See its module docstring and `docs/architecture.md` for the
full decision order; the short version: schema/evidence-ref validity is
checked first (failure -> `rejected`, unconditionally), then a fatal critic
objection or an explicit critic "rejected" recommendation (-> `rejected`),
then the volume/independence thresholds above (any shortfall -> `investigate`,
never `rejected` — a thin cluster may still be true, just unproven), and
only when everything clears does critic approval turn a card into
`experiment_ready`. `externally_validated` is not reachable from this
function at all (see `tests/unit/test_judge.py::test_externally_validated_is_not_a_reachable_status`)
— it requires imported behavioral evidence a human records outside this
pipeline entirely.
