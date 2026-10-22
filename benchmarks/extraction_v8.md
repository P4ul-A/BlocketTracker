# Extraction improvements — 22 October 2026

Implemented production extraction version 8 in `app/description.py` and the
documented tyre rules in `app/tyres.py`. The configured model is unchanged.
Restart/rebuild the running app to use the new code. Enabled background analysis
will invalidate old findings and refresh them from cached descriptions.

The model now selects up to four original sentence IDs. Validation accepts the
old single-ID format for offline response replay. Tyre rules independently scan
the available source text, apply inclusion/price/negation checks per seasonal
offer, and recover straightforward inventory facts even when the model misses
them. Unknown or contradictory wording stays unknown. Clear inventory assertions
are authoritative; unsupported model selections cannot bypass those rules.
Drivability behavior is preserved.

Each tyre decision retains original evidence sentences, a readable reason, rule
provenance, and a model-selection status (`validated`, `rule_override`, `rejected`,
or `unknown`). Additional sets can cite separate summer and winter inventory
lines. Highlights mark those quotes independently; the popup explains the rule.
The source description is never rewritten.

## Measured on the frozen development sample

| Measurement | Before | Version 8 |
| --- | ---: | ---: |
| Qwen3 1.7B: adverts with all four values correct | 32/60 | 60/60 |
| Included additional sets recovered | 0/18 | 18/18 |
| Included winter tyres recovered | 22/25 | 25/25 |
| Included summer tyres recovered | 10/20 | 20/20 |
| Incorrect positive tyre-inclusion findings | 2 | 0 |

Replayed all 180 previously recorded responses (three models × 60 descriptions)
through the final validator: every model's final facts matched all 60 labels.
These are **validation replays**, with no new inference or latency measurement.
They demonstrate how the app's rules correct these model omissions and errors.

Also ran 60 fresh Ollama requests using Qwen3 1.7B and the revised prompt/schema:
zero request/schema errors, and all 60 adverts matched the labels after final
validation. Observed mean latency was 1.99 seconds/advert; this session includes
background-worker contention and is not a controlled speed comparison.

Final rule refinements were revalidated against the fresh responses as well as
the original responses. This distinction matters because the live runner imports
the validation code once at startup. The final source hashes and counts are in
`verification_v8.json`.

All 180 replays reconstructed the original text exactly from highlight spans,
with no missing supported-quote highlights. Rendered three real detail pages
covering separate tyre inventory lines, optional winter wheels, and an AWD fault
without requiring a running server or touching the database. Interactive browser
behavior was not exercised. Also reviewed tyre decisions on additional cached
descriptions, which exposed optional package-price wording and future tyre needs.

The 60 labels guided these fixes, so 60/60 is a development-sample result rather
than a held-out accuracy estimate. Unusual wording, implicit context, incomplete
sets, and seller contradictions still require review. No automated tests were
added. No production database or notebook was edited.

## Artifacts

- `results_v8.jsonl`: fresh Qwen3 1.7B outputs and inference timings.
- `results_v8.metadata.json`: revised prompt, model information, initial hashes.
- `summary_v8.json`: fresh-run aggregate results.
- `replayed_v8.jsonl`: original three-model outputs with version-eight validation.
- `verification_v8.json`: final source hashes and validation/highlighting counts.

Rerun fresh inference into a new file:

```sh
.venv/bin/python benchmarks/benchmark_ollama.py qwen3:1.7b --output benchmarks/results_v8_repeat.jsonl
.venv/bin/python benchmarks/summarize.py --results benchmarks/results_v8_repeat.jsonl --output benchmarks/summary_v8_repeat.json
```
