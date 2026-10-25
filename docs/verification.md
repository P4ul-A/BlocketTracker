# Implementation verification

## Tyre inclusion and multiple evidence sentences — 22 October 2026

Extraction version eight adds field-specific tyre inclusion rules, extra-charge
and negation checks, seasonal abbreviation handling, and independently highlighted
source sentences. Deterministic inventory scanning recovers missed facts; raw
model selections, decision reasons, and validation outcomes remain reviewable.
The configured model and existing drivability behavior are unchanged.

Replayed the three-model benchmark's 180 recorded responses through the updated
validator. All models matched all 60 development labels, with no incorrect tyre
inclusion findings. A fresh Qwen3 1.7B run completed 60 requests using the new
multi-sentence schema without request/schema errors and matched all labels after
final validation. These labels guided implementation, so this is not held-out
accuracy. See `benchmarks/extraction_v8.md` for results and limitations.

Checked original-text reconstruction and quote highlighting on all 180 replays;
rendered three real detail pages for multiple inventory lines, separately priced
winter wheels, and AWD-only faults. Browser interaction was not exercised.
Production data and notebooks were not edited, and no automated tests were added.
Old findings refresh from cached descriptions after the updated app restarts,
provided background LLM analysis is enabled.

## Whole car links and missed Swedish driving denials — 22 October 2026

Extended the native title link across car cards in search results and favourites.
Favourite and map buttons sit above the link overlay, and keyboard focus outlines
the card. Rendered a favourite card from a real listing and confirmed one detail
link, a separate favourite button, and no buttons nested inside links. Browser
interaction and visual layout remain unverified.

Inspected the database read-only: 2,111 listings and 522 version-six cached
analyses. Advert 27330458 explicitly says the gearbox is broken and the car cannot
drive forward, 27061928 says `Startar ej`, and 26834194 says the engine currently
will not turn over. All three raw LLM selections were true with no source sentence,
so the existing validator defaulted to assumed drivability. The old patterns also
missed several ordinary Swedish constructions.

Revalidated all 522 cached raw responses in memory with the expanded rules. The
three missed listings became not drivable, with verbatim supporting sentences;
other cached drivability decisions remained unchanged. Scanning all available
descriptions found those three plus the two existing explicit non-drivable cars.
Checked that a non-starting diesel heater, a repaired historical fault, occasional
starting difficulty, an AWD-only fault, and refusal of a test drive alone did not
trigger a driving denial. Direct Swedish and English driving denials did trigger.
Rendered a corrected detail page with a red highlight and rule provenance; checked
that its spans reconstruct the original description and that filters and model
features both use `no`. The real heater-only advert 26119782 retained `yes`.

The prompt now asks for null when driving information is absent; application-level
default behavior stays unchanged. Extraction version seven invalidates old cache
entries for background re-analysis. Kept direct Swedish extraction rather than
adding translation, since translation would not address validator rejection.
Ollama was unavailable on localhost, so fresh model responses and translation
quality were not evaluated. Python/Jinja parsing, JavaScriptCore syntax, and
`git diff --check` passed. No tests or notebooks were written; live data was not
modified.

Performed on 21 October 2026 using Python 3.12 on the development Mac.
No automated test files were written and no notebooks were edited. All verification
observations were stored in a temporary `/tmp` database, separate from user data.

## Completed checks

- Retrieved the live upstream OpenAPI schema, two distinct car-search pages, and a
  car-detail response. Confirmed `/v1/search/car`, `/v1/ad/car?id=...`, pagination
  metadata, seller labels, vehicle attributes, coordinate fields, and Scandinavian
  mileage units. The upstream advert detail parser did not supply a description for
  the sampled advert; the detail page presents an explicit fallback and original link.
- Confirmed private adverts may carry `org_id`; explicit seller classification takes
  priority, and missing classification is not assumed private.
- Ran the local FastAPI server and exercised dashboard, search form/editor, result
  page, advert details, static Leaflet asset, health, and JSON endpoints: HTTP 200.
- Created a search, updated it with a private-seller criterion, and deleted the
  temporary CRUD search through the API: HTTP 201/200/200.
- Collected with a deliberately reduced two-page budget and two-detail-request limit.
  Initial run retained 101 usable training adverts and 73 exact-model purchase matches
  (12 private, 61 commercial). All 73 matches had map coordinates. The app reported
  partial coverage against approximately 3,809 upstream matches rather than claiming
  a complete collection.
- Trained the Ridge model on that live sample. Five-fold out-of-fold validation
  reported MAPE 20.3% and MAE 37,199 SEK for this small sample. These are verification
  observations, not promised model accuracy. Seller type appeared in the fitted
  categorical features. The model produced 97 estimates across qualifying model groups.
- Verified seller filters returned 12 private, 61 commercial, and zero unknown matches.
  The initial 10% threshold produced 20 highlighted purchase matches. Deal-only and
  price-sort endpoints returned successfully.
- Sent an inverted numeric range: HTTP 422. Sent a foreign-origin write: HTTP 403.
- Pointed a temporary collector at an unavailable endpoint. Bounded retries ended in
  an error status while all 73 active matches, history, and predictions remained stored.
- Stopped and restarted the server against the same database: the saved search,
  model metadata, and all 73 active matches persisted.
- Manually changed and restored one stored asking price in the temporary database;
  both changes appended history records, without replacing earlier observations.
- Called the application's town fallback for Kungsängen. It returned approximate
  coordinates and reused the cached result on a subsequent lookup. Recurring requests
  are spaced at least 15.1 seconds apart, globally through the single collector.
- Python compilation, Jinja template parsing, Python dependency consistency, and
  JavaScriptCore syntax checks for application JavaScript and Leaflet passed.
- Validated `compose.yaml` using the official standalone Docker Compose binary
  (`config --quiet`) with an existing temporary data directory. Configuration passed.
- Resolved and downloaded all runtime dependencies as Python 3.12 Linux AMD64 wheels,
  including NumPy, SciPy, and scikit-learn. No source compilation was needed.

## Checks still required on the NAS

No Docker engine, Dockhand instance, or browser-control facility was available in the
development environment. Consequently these checks remain deployment acceptance work:

1. Build the Linux AMD64 image and start it through Dockhand using your real dataset.
2. Confirm the numeric runtime UID/GID can create SQLite and its WAL files in `/data`.
3. Confirm the container health probe and HTTP access through the chosen NAS port.
4. In a browser, verify responsive layout, map tiles/attribution, marker/card selection,
   seller controls, and navigation on your preferred desktop/mobile devices.
5. Restart the container and confirm the bind-mounted database survives recreation.
6. Observe an unattended six-hour refresh and verify a stopped-container dataset backup
   can be restored. Scheduling logic was inspected; a six-hour wall-clock run was not performed.

The Docker build, NAS filesystem permissions, and visual browser interaction are not
claimed as verified by the local application and Compose checks above.

## Model transparency and year handling update

Reviewed the user's collected database read-only and made an isolated SQLite backup
for manual evaluation. The update retrains versioned models on startup and compares
three candidates using the same five advert-level folds. No user's live observations
were changed and no automated test files were added.

On 693 usable adverts, overall MAPE was 44.7% for the original linear Ridge model,
46.6% for flexible depreciation Ridge, and 35.4% for the selected random forest.
For the 671 XC70 adverts specifically, MAPE was 25.3%, 24.7%, and 14.9%, respectively;
XC70 mean absolute error improved from 27,850 SEK to 13,847 SEK. These figures use
model-selection folds and are not independent test-set performance. Sparse older
years still showed substantially larger errors than the newer, well-represented years.

The search page now includes selected model, factors, training/exclusion counts,
model-year response, candidate comparison, and make/model-specific year diagnostics.
The main percentage-error metric uses the selected make/model when available. The
panel explicitly warns when average prediction error exceeds the highlight threshold.
The structured variant label is now available to flexible candidates; condition,
service history, descriptions, equipment, and photos remain outside the model.

Python compilation, Jinja parsing, JavaScript syntax checks, and in-process HTTP
requests for `/api/model`, the search page, and listings succeeded against the
isolated upgraded database. A visual browser inspection remains outstanding.

## Deferred search edits

Manually exercised edit requests using an isolated temporary database and the
in-process HTTP interface. Editing a queued search returned 200 and retained one
queue entry. Repeated edits during a running refresh returned 200 with `deferred`
set, kept active criteria unchanged, and stored only the latest pending criteria.
The editor rendered those latest saved values. Applying the deferred update
activated the latest criteria and queued one follow-up refresh. Reinitializing
the database applied persisted edits and left the search pending for scheduling.
Python compilation and whitespace checks passed; no automated test files were added.

## Description fallback for advert 26487211

The live detail API omitted `description`, although the original Blocket page
contained it. Its description section uses a different CSS layout from the
upstream parser. Added heading-based HTML extraction as a fallback when the API
description is empty. Extraction retains line breaks, ignores the dealer biography,
and renders as escaped text in the existing listing template. Cached details from
the previous parser are eligible for an update without the normal 24-hour wait;
collection still respects its configured detail budget.

Manual extraction from the supplied advert yielded 161 characters over four lines,
including the repair-project, ABS, and odometer disclosures. Missing description
sections returned an empty value. The complete live API/fallback path retained
other API details. Python compilation and dependency consistency checks passed.
No automated test files were added.

## On-demand details for listing 27330458

The running local app returned a pre-fix cached empty description. The live HTML
fallback extracted 534 characters, but opening a listing previously only read SQLite.
Listing pages now refresh stale/missing details on demand, outside the batch budget.
Opened listings share the collector's serialized API request rate limit and reuse
fresh details for 24 hours. This enrichment updates neither `last_seen` nor price history.

Using an isolated copy of the user's database, an in-process request to the listing
page returned 200 and rendered all 534 description characters. A second visit reused
the cache. Last-seen timestamps and history counts stayed unchanged. Python compilation
and whitespace checks passed. No automated test files were added.

## Search isolation and model-name variations

The XC90 screenshot displayed shared training totals and overall error despite no
exact-match XC90 listings. The search page now scopes its main sample, errors, and
candidate comparison to the requested make/model. Shared totals are explicitly
labeled in the expanded panel; no overall-error fallback is used for a new model.
The listings endpoint also checks current criteria before returning memberships.

Whitespace/case variations such as XC 90 and XC90 now share matching and diagnostic
identity. Short letter/number queries are joined before searching upstream. Distinct
models, suffixes, and meaningful punctuation remain separate. Model version four
rebuilds diagnostics on startup with the consistent keys.

Manually checked API results against an isolated database copy. An uncollected model
returned no scoped statistics or candidate errors; a deliberately mismatched listing
membership was excluded. A live XC90 query returned 50 matching cars for an XC 90
criterion. Python compilation, template parsing, JavaScript syntax, and whitespace
checks passed. No automated test files were written.

## External Ollama description facts

Implemented six evidence-backed description fields, saved and toolbar filters,
listing badges and evidence panels, cached background extraction, and yes/no/unknown
price-model features. Ollama is external to the tracker. `.env` and `.env.example`
use Docker Desktop's `http://host.docker.internal:11434` for a Mac-hosted Ollama;
URL, model, enablement, timeout, and CPU-thread count are environment settings.
There is no Ollama service or automatic model download in the tracker stack.

Downloaded the selected Qwen3 1.7B model to the existing local Ollama for manual
verification. An earlier run against an already-installed different small model
missed useful findings; the selected model and evidence checks were then verified
against the repair-project advert and two temporary Swedish examples. The repair
advert yielded repair needs, odometer fault, and winter tyres, while starting and
an additional set remained unknown. The healthy example identified starting and
included summer/winter/additional sets. The non-starter example identified inability
to start and did not mark separately priced winter tyres as included. These spot
checks do not establish general extraction accuracy; some explicitly stated facts
can still be missed and remain unknown.

Using an isolated temporary database, exercised filter endpoints for repair,
non-starting, additional tyres, and explicit starting, plus saved-filter dashboard
counts and the evidence page. Checked changed-description cache invalidation,
unsupported-quotation rejection, and unavailable-service error/cooldown behavior.
A separate isolated copy of the real collected dataset trained the updated price
model on 2,036 adverts with an analyzed description included. This verifies feature
integration, not an accuracy improvement from the still-small labeled sample.

Template parsing, JavaScriptCore syntax, Python compilation, dependency consistency,
and Docker Compose configuration passed. No automated test files were added. Browser
visual inspection, container rebuild, and connectivity to a future NAS/external Ollama
address remain unverified. Host Ollama HTTP extraction was verified on this Mac.

## Description filter grid

Replaced the finding/value dropdowns with a six-row radio grid offering Any, Yes,
No, and Unknown per finding. Active rows combine with AND and further narrow saved
criteria. The API accepts multiple `fact_<name>=yes|no|unknown` parameters while
retaining the earlier single-fact interface. Rapid changes request a follow-up load
when an earlier results request is in progress.

Manually rendered the page against the isolated description-review database: 24
radio options were present and the old dropdown was absent. Combining repair=yes
and non-starting=yes returned the expected example; an invalid value returned 422.
JavaScript syntax, Python compilation, and whitespace checks passed. No automated
test files were added. Visual browser inspection remains unverified.

## Inline description highlights

Removed the separate findings card. Description evidence is highlighted in place,
with red-tinted problems and green-tinted extras/positive assertions. Overlapping
quotations are split at their boundaries without duplicating source text. A small
accessible top-right icon uses amber for pending, green for done, red for retry
errors, and grey for disabled/missing descriptions. Polling refreshes the page when
status changes, including failure transitions.

Manually rendered ready/pending states using the isolated example database. Ready
produced three evidence highlights; pending produced none, and the standalone card
was absent. A standard HTML parser confirmed original characters and paragraph
breaks were preserved. Checked overlapping evidence and escaped HTML content.
Python compilation and whitespace checks passed; no automated test files were added.
Visual browser inspection remains unverified.

## Clickable highlight decisions

Highlights now open an accessible native dialog with each associated fact, the
model's yes/no decision, and the supporting quotation. Overlapping highlights
include all related decisions. Mouse/touch, Enter/Space, Escape, a close button,
and outside-click dismissal are supported; closing restores highlight focus.
Rendered the isolated example and checked three clickable highlights, valid encoded
decision payloads, keyboard attributes, unchanged source text, and JavaScript syntax.
No automated tests were added; visual browser interaction remains unverified.

## Missed non-drivable advert and revised condition category

Located advert 27506467 in the local database with all-unknown cached version-one
facts. The old raw response was not retained, so replayed the same description in
an isolated copy. That replay returned a stitched multi-sentence quotation for
repair needs and an unrelated odometer quote for non-starting. Validation rejected
both, reproducing the unhighlighted result.

Replaced Repair/Does not start with **Drivable** (yes / no / unknown), including
all causes of non-drivability rather than only engine trouble. The extraction model
now selects numbered source sentences; supporting text is resolved directly from
the original description instead of accepting generated quotations. Raw sentence
selections are saved for future investigation. Extraction version three invalidates
old cached facts, and price-model version seven uses the new category. Obsolete
saved filters are removed with a review notice rather than silently translating
repair needs into non-drivability; other saved finding filters remain intact.

The selected Qwen3 1.7B model classified this exact advert as drivable=false and
selected 'Måste tas med släp då den inte är körbar.' as evidence. Manually rendered
the red highlight and its popup payload, checked the yes/no filters and model input,
and confirmed original text remained unchanged. Additional manual examples yielded
yes for explicit daily driving, no for gearbox failure permitting only reverse,
and unknown for an ABS lamp plus a valid inspection without driving information.
An isolated legacy-filter initialization retained the tyre filter and showed a
condition-filter review notice. JavaScript syntax, Python compilation, and whitespace
checks passed. No automated test files were added. Live user data was inspected
read-only; re-analysis will run on the user's next deployment.

## Above-estimate price highlighting

Added red card borders, percentage badges, and map markers above the configured
threshold, symmetric with the existing green below-estimate marking. The highlights
count and highlights-only filter include both directions. Manually exercised an
isolated database with an estimate of 100: asking 90 was green, 110 was red, 109.99
was unmarked, and missing price was unmarked. Both threshold cases appeared in the
highlights-only response. Python compilation and whitespace checks passed; no
automated test files were added. Browser visual inspection remains unverified.

## Unchanged price history line — 22 October 2026

The chart used only price-change timestamps, so a car seen repeatedly at the same
price displayed a single dot. It now extends the final price to the listing's
last-seen timestamp, giving unchanged prices a horizontal line and changed prices
a final horizontal segment. A genuine single sighting still displays a dot. The
chart does not extend to the current time or past an unavailable-price observation.
Existing stored data works immediately; no migration or extra collection is needed.

Read the local database without modifying it and generated charts for all 2,111
listings: 2,109 now have a line, and two have a single timestamp. Confirmed a real
7,000 SEK listing renders a flat path with distinct first/last sighting labels,
and an existing price-change history retains its vertical step. Rendered the exact
timestamps from the user's screenshot: 21 October at 19:42:57 through 22 October at
09:04:17, Stockholm time, with two 7,000 SEK endpoints and no single-sighting notice.
Also manually checked empty history and a later unavailable-price observation.
Python/Jinja parsing and `git diff --check` passed. No tests or notebooks were
written. Browser visual inspection remains unverified.

## Smooth price history and collection tooltips — 22 October 2026

Replaced the step path with cubic curves through the observed prices. Each segment
stays within its endpoint prices, and unchanged prices remain horizontal. Points
now have larger hover targets and immediate tooltips showing the price and collection
date/time in Stockholm time. Keyboard focus reveals the same details. Removed the
chart-wide native title tooltip so it does not compete with point details.

Manually rendered a price drop with a later unchanged sighting, empty history, a
single sighting, an unchanged price, and a later unavailable price. Python parsing,
Jinja rendering, SVG/XML parsing, and whitespace checks passed. No tests or
notebooks were written. Browser visual inspection remains unverified.

## Settings task log — 22 October 2026

Added persistent task runs, ordered subtasks, and temporary run/listing membership
to SQLite. Each queued search refresh owns one run covering purchase/comparable
collection, initial model training, LLM analysis, and retraining. The independent
LLM worker attributes shared requests/batch retraining to all owning search runs.
Unowned saved descriptions use one background backfill run; startup model training
is logged separately. Parent elapsed includes inter-stage waits; stage times sum
active calls, and initial queue wait is separate. Running, waiting, completed,
partial, failed, skipped, and interrupted states are visible in Settings, with
live counters and five-second polling. Finished details retain their timestamps
and progress/error messages. The API shows every active run and the latest 50
finished runs; older history remains stored. Unclean restart recovery closes runs
at their last persisted activity rather than adding unknown downtime.

Manually exercised the real collector worker and analyzer against an isolated
temporary database using a recorded advert/page and recorded Ollama decisions.
Two searches produced two task runs with all four stages, while their shared
listing required one inference request. Confirmed running analysis state during
the request, accumulating stage times, and successful completion after retraining.
A replayed HTTP 503 left analysis waiting with its error/progress message and
respected the existing retry cooldown. A failed retraining operation was retried
before closing the run, retaining its partial-collection outcome. Disabled analysis
skipped both LLM stages; a collection outage marked collection/run failed and later
stages skipped. A timing example accumulated five active seconds across ten elapsed
seconds; simulated crash recovery excluded downtime. Reinitializing the database
retained completed/interrupted history. Confirmed the 50-finished-plus-active limit.
Startup lifecycle, Settings HTML, and task-history API returned successfully.

Python/Jinja parsing, JavaScriptCore syntax, and whitespace checks passed. No test
files or notebooks were written, and the user's live database was not modified.
Fresh external-service responses and browser visual interaction remain unverified.

## Docker startup migration — 22 October 2026

The container was repeatedly restarting with `sqlite3.OperationalError: no such
column: updated_at`. An existing `task_runs` table predated the heartbeat column;
`CREATE TABLE IF NOT EXISTS` did not upgrade it. Startup now adds the missing
column and fills it from the latest saved run/stage activity before recovering
interrupted tasks. Existing task history is retained.

Saved a SQLite backup before rebuilding. Manually ran initialization twice and
interrupted-task recovery on a copy of the existing database: both saved runs
were retained, timestamps were populated, and task history remained readable.
Rebuilt and started the Compose service; Docker reported healthy, startup completed,
and `/health` and `/api/settings/tasks` returned HTTP 200. Background Ollama requests
also returned HTTP 200. `git diff --check` passed. No tests or notebooks were written.

## Price confirmations, gradual history thinning, and results filters — 25 October 2026

Each listing seen during a search refresh now records a price confirmation even
when unchanged. Repeated page entries and cached-detail enrichment do not add
extra sightings. The chart renders the recorded confirmations directly. Startup
preserves the known last-seen endpoint from legacy changes-only histories; earlier
unchanged confirmations cannot be reconstructed.

History is capped by `PRICE_HISTORY_LIMIT` (default 200, minimum 2). The first
observation and newest half of the limit are protected. Older points are removed
gradually by their contribution to the price curve, preferring close neighbours
on ties. This preserves long-term coverage and prioritizes significant changes.
The same compaction applies at startup when the configured limit is lowered.

Added results-only inclusive year, kilometre, and SEK ranges plus manual/automatic
selection. Apply and Clear affect the listings API used by both cards and map,
combining with existing toolbar controls without modifying saved criteria.

Manually exercised an isolated temporary database: 450 confirmations compacted to
200 points while retaining the first observation, an older price spike, and all
100 newest confirmations. The SVG rendered 200 dots; enrichment left history and
last-seen unchanged. Combined filters returned the expected cars, including Swedish
transmission labels. Inverted ranges, negative mileage, and unsupported transmission
values returned HTTP 422. Search HTML rendered six range inputs, saved criteria
remained unchanged, and repeated startup preserved the legacy endpoint without
duplicating it. Python/Jinja parsing, FastAPI registration, JavaScript syntax,
Compose configuration, and whitespace checks passed. No test files or notebooks
were written. Live data and deployment were not modified; browser interaction
remains unverified.
