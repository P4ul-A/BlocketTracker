# Ollama benchmark on cached Blocket descriptions

The initial version-seven benchmark below is retained for comparison. The
subsequent extraction fixes and version-eight results are documented in
[extraction_v8.md](extraction_v8.md).

Measured locally on 2026-10-22, on an Apple M2 Pro (10 CPU cores, 16 GB
unified memory). No Blocket requests or production database writes are made.
Production configuration is unchanged.

## Findings and recommendation

**The installed 4B model does not justify its extra inference cost for the
current extractor. The 2B model is a viable smaller Qwen3.5 option for coarse
screening, but none of these models is sufficient for dependable tyre filters
with the present prompt and validator.** Keeping the current Qwen3 1.7B baseline
is reasonable until those extraction issues are addressed; simply moving to
Qwen3.5 2B was not a clear quality improvement.

Every model completed all 60 requests with schema-valid responses and no HTTP
errors. Results below are after production validation and driving-denial rules.

| Local model tag | Mean seconds/advert | Median | p95 | All four values correct, random 40 | All four correct, full 60 | Correct fields, full 240 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| `qwen3:1.7b` | 1.55 | 1.38 | 2.35 | 29/40 (72.5%) | 32/60 | 197/240 |
| `qwen3.5:2b` | 2.76 | 2.58 | 4.24 | 30/40 (75%) | 33/60 | 198/240 |
| `qwen3.5:4b` | 4.43 | 4.01 | 6.95 | 30/40 (75%) | 36/60 | 193/240 |

The 2B run used 37.6% less time than 4B. Compared with the existing 1.7B,
2B took 78.2% more time while improving only one exact advert in each sample.
The one-advert differences in the random sample are too small to establish a
robust ranking. The diagnostic sample favors 4B on exact-advert matches while
2B has more correct individual fields; neither dominates.

Positive tyre recall exposes weaknesses hidden by correct unknowns:

| Included fact recovered | Qwen3 1.7B | Qwen3.5 2B | Qwen3.5 4B |
| --- | ---: | ---: | ---: |
| Additional tyre set | 0/18 | 5/18 | 5/18 |
| Winter tyres | 22/25 | 21/25 | 11/25 |
| Summer tyres | 10/20 | 6/20 | 13/20 |
| Incorrect positive tyre-inclusion decisions | 2 | 4 | 1 |

All three models identified all five non-drivable descriptions as false in their
raw responses, and all final drivability values matched the 60 labels. Every
negative decision also has a deterministic-rule override. Most positive labels
use the assumed-Yes policy, so 60/60 drivability is not proof of a generally
reliable model. AWD-only failures correctly remain drivable after validation.

The most consequential observed problems are:

- **Paid extras become included.** Listing `25389375` says an extra set can be
  purchased from 3,995 SEK; 2B nevertheless returned extra-set=true. Listings
  `27459397` and `27587340` offer winter wheels separately; 1.7B returned
  winter=true for both, and 2B returned true for the first. 4B also counted a
  conditional paid protection-package tyre set as included (`23803263`).
- **Obvious tyre facts disappear.** For `15538126`, “Sommar-och vinterhjul
  medföljer”, 4B recovered all three tyre facts whereas 1.7B omitted the extra
  set. Across the full sample, 1.7B never returned an additional-set fact.
- **Some misses are imposed by evidence design.** Separate summer and winter
  inventory lines cannot support one combined additional-set statement under
  the current single-sentence requirement. The extra-set validator also checks
  full `sommar`/`vinter` words and rejects abbreviations such as S- and V-hjul.
- **Tyre evidence validation is too broad.** For listing `25124524`, 4B returned
  summer=false using “Fyrhjulsdriften fungerar ej, har inte felsökt.” The validator
  accepted this because `hjul` occurs inside `fyrhjulsdriften`. It does not verify
  seasonal identity, inclusion, or polarity. Structured JSON and literal evidence
  alone therefore do not guarantee correct decisions.

Before relying on these filters, improve inclusion/extra-charge checks and
season-specific evidence validation, and allow both inventory sentences to
support an additional set. Then rerun this frozen benchmark. This investigation
does not implement those behavior changes.

The downloaded 2B tag occupies 3.12 GB on disk, versus 3.97 GB for the installed
4B and 1.36 GB for 1.7B. Settled `/api/ps` observations at context 8,192 reported
approximately 4.61 GB resident for 2B, 7.77 GB for 4B, and 1.99 GB for 1.7B.
These are server-reported snapshots, not peak whole-process memory measurements.
The 4B settled snapshot is retained inside `qwen3.5_2b.resident.json`; the later
2B snapshot is in `resident_during_2b.json`.

## Reproduce

From the project root, with Ollama running and the models installed:

```sh
.venv/bin/python benchmarks/benchmark_ollama.py qwen3:1.7b qwen3.5:4b qwen3.5:2b
.venv/bin/python benchmarks/summarize.py
```

The runner skips completed model/advert pairs in `results.jsonl`. Move that file
aside to run a new measurement; do not mix separate repetitions in the same file.
The Python scripts are benchmark utilities, not an automated test suite.

## Method

The read-only SQLite snapshot contained 2,111 listings, 522 nonempty descriptions,
and 488 distinct nonempty descriptions. `cached_sample.json` freezes 60 distinct
original texts with listing IDs: 40 sampled uniformly without replacement using
`random.Random(42)` from distinct descriptions ordered by their first listing ID;
17 additional cases chosen for tyre inclusion, separately priced extras, driving
faults, AWD faults, and abbreviations; and the three longest descriptions.
One non-drivable advert was already in the random group. The five descriptions
detected by the application's driving-denial rules are all represented.

`labels.json` contains independently reviewed expected effective values from the
seller texts, rather than treating either model or old extraction caches as
ground truth. Ordinary equipment lists count as included inventory, separately
priced sets are excluded, and omitted tyre information is unknown. Drivability
is judged after the application's assumed-Yes policy. These are seller assertions,
not independently verified vehicle conditions.

The production prompt and JSON request are read directly from
`DescriptionAnalyzer.analyze` using Python's AST. Every request therefore uses
the app's schema, numbered source sentences, `think=false`, temperature 0,
requested context 8,192, prediction limit 900, and two CPU threads. The production
validator and driving-denial overrides are applied to every response. Model names
are the only request override. Each model is warmed with a short request before
its 60 sequential extraction requests; warmup is excluded from latency statistics.
The live request duration includes HTTP overhead and any server scheduling.
Server timing/token statistics are retained where supplied.
Ollama also served the application's background Qwen3 1.7B worker during this
session (confirmed by a separate recently active resident model during the 2B
run). Timings therefore include possible resource contention; they are practical
observations, not measurements from an otherwise idle dedicated server.

Report both random-group scores and the full diagnostic sample: the enriched
sample overrepresents difficult cases. Correct fields include unknowns and
assumed drivability, so also inspect positive tyre recall and exact-advert scores.
Semantic tyre labels span the whole advert; the production constraint requiring
one supporting sentence can make an extra-set fact unrepresentable when summer
and winter wheels occur on separate lines. Such misses reflect the whole app
pipeline, not necessarily a model error.

## Limits

This is one deterministic pass per model on one Mac, with 60 manually labelled
adverts and only five non-drivable examples. It cannot establish general safety,
NAS/CPU performance, accuracy on uncached searches, or improvements to downstream
price predictions. Prompt-prefix caching remains enabled as in normal app use.
Inference results are saved separately and never replace production analysis.

These installed tags use different inference implementations and quantizations:
Qwen3 1.7B uses GGUF Q4_K_M; Qwen3.5 4B uses MLX NVFP4; the newly downloaded
Qwen3.5 2B defaults to MLX MXFP8. This measures the local tags as actually served,
not an isolated comparison of parameter counts. Inspect the recorded model
digests and `/api/ps` observations before comparing other machines. Initial
warmup requests use the server's default context, while subsequent full app
requests increased the observed context to the requested 8,192 for both MLX
models. Compare settled observations rather than warmup memory figures.

The requested Qwen3.5 1.7B tag does not exist in the official
[Ollama catalogue](https://ollama.com/library/qwen3.5/tags); 2B is the nearest size.

## Artifacts

- `results.jsonl`: individual outputs, raw selections, validated facts, and timing.
- `results.metadata.json`: server version, installed model digests, and prompt.
- `*.resident.json`: observed resident memory and warmup information.
- `summary.json`: aggregate scores and every mismatch with its evidence.
- `labels.json` and `cached_sample.json`: reviewable scoring basis and source data.
