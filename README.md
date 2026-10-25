# Blocket Tracker

A personal car tracker for an Intel-based HexOS NAS. One Docker container serves a
FastAPI website, collects Blocket car adverts, keeps price history in SQLite, and
learns an asking-price model. Manage the Compose stack through Dockhand.

## What it does

- Multiple saved car searches with make/model, keywords, price, year, mileage,
  region, transmission, fuel, and private/commercial/unknown seller filters.
- Listing cards and an interactive OpenStreetMap map, with town-level geocoding
  when advert coordinates are missing.
- Results-only year, mileage, price, and manual/automatic filters, with Apply and
  Clear actions. These narrow the cards and map without editing the saved search.
- Six-hour refreshes, manual refresh, clear collection status, and retained history.
- Broader car comparables for training, independent of budget, region, and seller
  selection. Seller type is a model feature as well as a filter.
- Configurable price highlights: green below and red above the estimate, defaulting
  to a symmetric 10% threshold. Cards, badges, and map markers use the same colors.
  “Highlights only” includes both directions.
- Price history saves one confirmation per listing seen in each search refresh,
  including unchanged prices, and draws a dot for each recorded price. Hover over
  a point or focus it with the keyboard to see its price and collection timestamp
  in Stockholm time. Curves between observations are illustrative. Duplicate page
  results, cached-detail enrichment, and page visits do not add confirmations.
  Each listing retains at most `PRICE_HISTORY_LIMIT` observations (default 200,
  minimum 2). The first point and newest half of the limit are preserved; older
  history is gradually thinned, prioritizing significant price changes and wider
  time spacing. This keeps long-term trends at lower resolution while recent
  confirmations retain full detail. Retained points are real observations. A lower
  configured limit also compacts existing history at startup. On upgrade, the
  previous last-seen endpoint is preserved where its price is known, but earlier
  unchanged sightings cannot be reconstructed because they were never stored.

## Deploy on HexOS / Dockhand

See [the deployment guide](docs/deployment.md) for dataset permissions, Dockhand
setup, upgrades, and backups. The target is **linux/amd64**, suitable for your Core i5.

1. Put this repository on your NAS, in a directory Dockhand can use as the stack's
   build context. Keep the application source separate from the data dataset.
2. Create a dataset directory for persistent data. Grant the configured numeric
   UID/GID write and traversal access. The default container identity is `10001:10001`.
3. Copy `.env.example` to `.env`. Set `DATA_PATH` to the **existing absolute NAS
   directory**, and set `PUID`/`PGID` to the identity allowed to write there.
4. Deploy `compose.yaml` through Dockhand with that environment and source build
   context. Build the image before starting the service. If Dockhand cannot build
   a local context, run `docker compose build` in the repository directory first,
   then deploy the same Compose stack through Dockhand.
5. Open `http://YOUR-NAS-IP:8080`, create a search, and let the first refresh finish.

Equivalent shell commands from the repository directory:

```sh
cp .env.example .env
# Edit .env before continuing.
docker compose config
docker compose up -d --build
```

The website has **no login** and is intended for your LAN or VPN. Do not expose its
port publicly. You do not need a GPU, database server, API key, or cloud hosting.

## Local development

Python 3.12 is recommended. Local development writes to `./data` by default:

```sh
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/uvicorn app.main:app --host 127.0.0.1 --port 8080 --workers 1
```

Use one worker and one application replica: the scheduler and collection queue
are intentionally in-process. Interactive API documentation is at `/docs`.

## Search behavior

Choose a make/model (for example `VOLVO` / `V60`, or `TESLA` / `Model 3`).
The Blocket API's `models` parameter actually selects a make. The application
normalizes common model spacing in queries and compares structured model names
without spaces or case differences. `XC 90`, `XC90`, and `xc90` match automatically;
distinct models and suffixes remain separate.
Extra keywords are sent upstream. Region and transmission use the documented API
filters; fuel is a case-insensitive substring of the Swedish advert fuel label.

Prices are SEK. Mileage is displayed, stored, filtered, and modeled in **kilometres**.
Swedish mil from Blocket are automatically multiplied by 10; kilometre inputs stay
unchanged. Unknown upstream units remain unavailable rather than guessed. Existing
listings and saved mileage limits are converted once at startup. The JSON fields
`milage_from` and `milage_to` retain their legacy spelling but now take kilometres.
The adapter converts these bounds back to whole Swedish mil for upstream queries,
rounding outward and enforcing exact kilometre limits locally.
Numeric filters exclude records without the relevant measurement.

Seller type uses explicit upstream classification. Live responses show that private
sellers can also have an `org_id`, so that field alone is **not** treated as proof of
a commercial seller. Unknown classifications stay unknown. The upstream detail
parser's default-to-private fallback is not trusted without an explicit search signal.
The results-page seller filter further narrows the saved search's existing matches.
The results-only year, mileage, and price ranges are inclusive, and transmission
can be narrowed to manual or automatic. Apply combines these filters with the
existing seller, description, and highlight controls; Clear removes the numeric
and transmission filters. No new upstream search or model retraining is triggered.

A refresh visits purchase-search pages, enriches up to `DETAIL_LIMIT` adverts,
then collects broader make/model comparables. It does not apply purchase filters to
those comparables. Details are cached for 24 hours. The oldest/missing details are
prioritized so later runs fill gaps. If the detail API omits a description, the collector reads only the description
section from the original Blocket advert, preserving line breaks. Opening a listing fetches missing or outdated details immediately, independently of
the batch detail budget. Subsequent visits reuse the 24-hour cache. Existing cached
details from the older parser are eligible immediately, and batch refreshes also
update them within their configured detail-request budget. Fallback failures keep other advert data and
any previously collected description. Summary records remain useful for model training
when description or equipment is unavailable. Original adverts remain linked.

The API currently exposes up to 50 pages. If the total exceeds accessible pages,
results repeat, the API fails, or the configured page budget is reached, the app
reports incomplete coverage. Pagination is not a snapshot, so adverts moving
between pages can also affect coverage. A failed or partial refresh never archives
previous matches. After a fully completed refresh, missing matches are labeled
“not seen”; that does **not** mean sold. Archived matches can be shown in results.

Manual refreshes are queued immediately and duplicate requests are coalesced.
Automatic rescans run at selected daily hours in Stockholm time. Missed hours are
skipped after restart. A failed refresh retries at the next selected hour; use
manual refresh to retry sooner.

## Asking-price model

The application compares three scikit-learn models on log asking price: the original
linear Ridge baseline, Ridge with flexible depreciation slopes and make/model-specific
year/mileage adjustments, and a small random forest. The forest can learn year ranges
and interactions rather than one percentage change per year. All use make, model,
year, mileage, fuel, transmission, power when available, and seller type. The flexible
candidates also use the structured variant/trim label. A complex model must improve
held-out MAPE by at least 2% relative to the baseline to be selected. Selection uses
the same validation folds, so the reported errors are exploratory, not independent
test-set accuracy. Model code changes trigger retraining on startup, without waiting
for the next collection. The model panel shows the selected algorithm, factors,
comparison errors, and per-year accuracy for the selected make/model.
Numeric features are scaled; categorical and missing values are encoded explicitly.
It retrains when usable observations change, using one current record per advert
seen within 180 days. Repeated observations of an unchanged advert do not increase
its training weight. Historical prices are retained separately for inspection.

Estimates require at least **100 usable adverts overall** and **20 adverts for the
exact make/model**. Price, make, model, year, and known mileage are required. Five-fold
validation holds out whole adverts, including refitting the feature encoder. Displayed
estimates use these out-of-fold predictions: a listing's own price is not used to
predict its estimate. Model metadata, per-year diagnostics, and final-fit coefficients for Ridge models are
stored as JSON in SQLite; no pickle files are loaded.

The dashboard reports sample count, mean absolute percentage error (MAPE), and mean
absolute error in SEK. These describe the available sample, not a confidence interval
or a guarantee for a particular car. There is no additional accuracy gate for highlights.
The market sample can be biased by API page limits, stale asking prices, duplicate
relisted vehicles under new advert IDs, unobserved condition, and missing equipment.
Photos and equipment lists are retained for human review. When local LLM analysis
is enabled, extracted description facts are included in the structured price model. The structured variant label is used by flexible candidates. **Final sale prices are not available.**

## Configuration

| Variable | Default | Meaning |
| --- | --- | --- |
| `DATA_PATH` | Required in Compose | Existing persistent NAS directory |
| `PUID` / `PGID` | `10001` / `10001` | Numeric runtime identity with dataset write access |
| `PORT` | `8080` | NAS HTTP port |
| `MAX_PAGES` | `50` | Pages per purchase/broad query, maximum 50 |
| `DETAIL_LIMIT` | `100` | Maximum detail requests per search refresh; zero disables them |
| `PRICE_HISTORY_LIMIT` | `200` | Maximum observations per listing, minimum 2; retain recent detail and progressively thin older history |
| `REQUEST_DELAY` | `0.5` | Minimum seconds between Blocket requests, at least 0.2 |
| `GEOCODING` | `true` | Enable town-level fallback geocoding |
| `GEOCODER_URL` | Public Nominatim search endpoint | Switch geocoding provider without changing code |
| `GEOCODER_USER_AGENT` | Identifying application string | Set a descriptive identifier, optionally a contact address |
| `DATA_DIR` | `./data` locally, `/data` in Docker | SQLite directory |
| `BLOCKET_API_URL` | `https://blocket-api.se` | Backend-only upstream override for development |

## JSON interfaces

- `GET /api/options`: supported make, region, and transmission enums.
- `GET/POST /api/searches`; `PUT/DELETE /api/searches/{id}`: saved criteria.
- `POST /api/searches/{id}/refresh`: asynchronous refresh (`202`).
- `GET /api/searches/{id}/listings`: results; optional `seller`, `deals`, `archived`,
  `sort=discount|price|newest`, `year_from`, `year_to`, `mileage_from`, `mileage_to`,
  `price_from`, `price_to`, and `transmission=MANUAL|AUTOMATIC` query parameters.
  Result ranges use kilometres and SEK, reject inverted bounds, and only narrow
  the saved search's matches.
- `GET /api/listings/{id}`: normalized details and observed price history.
- `GET /api/settings/tasks`: running/queued tasks and the latest 50 finished runs,
  including subtask statuses, timestamps, progress messages, and durations.
- `GET /api/status`, `GET /api/model`, `GET /health`: collection, model, and health.

Edits save immediately. If a refresh is running, the latest edit is persisted and
applied when it finishes, then exactly one refresh is queued with the new settings.
Repeated edits replace the pending settings; queued refreshes use the latest settings.
Deferred edits survive restart. Delete returns `409` while a search is queued or
running. Numeric range validation returns `422`. Deleting a search retains shared observations/history.
Cross-origin writes are rejected. The API remains unauthenticated for personal use.

## External services and attribution

The unofficial [Blocket API](https://blocket-api.se/) supplies search and advert
information; it is not operated by Blocket AB. The adapter was checked against
`/swagger/openapi.json` and live search/detail responses during implementation.
No seller contact details are separately scraped.

Maps use [OpenStreetMap](https://www.openstreetmap.org/copyright) data and standard
tiles, with attribution shown on the map. Town geocoding uses
[Nominatim](https://operations.osmfoundation.org/policies/nominatim/), cached by town,
with one sequential request per 15.1 seconds (at most four per minute for recurring jobs), no autocomplete, and Sweden-only queries.
Negative results retry after 30 days. Failed lookups leave adverts in the list.
Follow the [tile usage policy](https://operations.osmfoundation.org/policies/tiles/);
this is a small personal-use app, not an offline tile downloader.

Leaflet 1.9.4 is vendored under its [BSD 2-Clause license](app/static/vendor/LEAFLET-LICENSE).
Maps require internet for tiles; the library itself is served locally.

## Verification

No automated tests or notebook edits are included, following the repository instructions.
See [verification notes](docs/verification.md) for executed checks and remaining NAS checks.

The search page shows only its make/model’s trained comparable count and accuracy.
An uncollected model shows zero comparables and “Not ready”, rather than another
model’s error. Shared training totals remain labeled separately in the model panel;
shared history is intentionally retained for learning. Listings are linked to their
saved search and checked against its current criteria before display.

## External Ollama description analysis

The tracker connects to your separately installed Ollama. It does not start an
Ollama container or download models automatically. Defaults support Docker Desktop
on a Mac with Ollama installed on the host:

```dotenv
LLM_ENABLED=true
LLM_URL=http://host.docker.internal:11434
LLM_MODEL=qwen3:1.7b
LLM_TIMEOUT=180
LLM_THREADS=2
```

Put these values in `.env` alongside `compose.yaml`. If `.env` already exists, add
or update the entries; do not overwrite your dataset settings. On your Mac, start
Ollama and pull the configured model once:

```sh
ollama pull qwen3:1.7b
DATA_PATH="$PWD/data" PUID="$(id -u)" PGID="$(id -g)" docker compose up -d --build
```

`host.docker.internal` reaches the Mac from a Docker Desktop container. For native
Python development, use `LLM_URL=http://localhost:11434` instead and export
`LLM_ENABLED=true`; the native app does not load `.env` automatically. Set
`LLM_ENABLED=false` to disable background analysis while retaining cached facts.

On the NAS, change `LLM_URL` to the externally running Ollama server, for example
`http://192.168.1.50:11434`. It must listen on an interface reachable from the
tracker container. If Ollama runs on that Linux Docker host itself, use its reachable
LAN IP or explicitly configure a Docker host-gateway alias; the Docker Desktop name
is not automatically provided by every Linux Docker installation. No additional
service is added to the tracker stack.

`LLM_MODEL` must name a model already installed on the external server. The default
Qwen3 1.7B download is about 1.4 GB. The integration requires Ollama's chat,
JSON-schema output, and `think=false` support. `LLM_TIMEOUT` defaults to 180 seconds;
`LLM_THREADS=2` leaves capacity for other work on the Ollama host. Description text
is sent only to the configured server; no cloud LLM or API key is required.

The background worker automatically processes existing and newly collected descriptions
sequentially. It recognizes four facts: **drivable**, **additional tyre set**, **winter tyres included**, and
**summer tyres included**. Each is **yes**, **explicitly no**, or **unknown/not stated**.
For **Drivable**, Yes is assumed unless a supported seller statement explicitly
establishes that the car cannot be driven. This default also applies to missing
descriptions and pending, disabled, or failed analysis. No requires evidence such
as “inte körbar”, mandatory trailer transport, or a disabling engine/gearbox fault.
Assumed drivability has no card badge, supporting quote, or text highlight. Filters and price-model features use the same default. Unknown is not
used for drivability. Other facts still use unknown when unstated. Tyres
available for an extra charge should not be classified as included.

Click anywhere on a car card to open its details, including its photo and empty
space. Favourite and **Show on map** buttons keep their own actions; the card link
also supports keyboard navigation and opening in a new tab. This applies to both
search results and favourites.

Listing cards highlight positive findings. Listing details highlight supporting seller text directly in the description:
not-drivable statements use a red tint; drivable statements and
included extras use green. Click or tap a highlight to see its extracted decision and supporting quotation.
Keyboard users can press Enter or Space; Escape closes the popup. A small top-right icon shows pending (amber), done
(green), unavailable (red), or disabled (grey); hover for status details. Saved-search filters can combine multiple facts; the results grid
can combine Any/Yes/No/Unknown choices across multiple findings. Filters apply locally after collection, so they
do not restrict the broader training sample. A strict “no” filter requires an explicit
negative statement; for other facts, select “unknown” to find adverts without a supported assertion.

The extractor receives numbered source sentences and selects up to four supporting
sentence IDs for each decision. It returns null for missing driving information; the
application applies the assumed Yes default after validation. Evidence comes
directly from the original sentences, preventing rewritten quotations. Each
supporting sentence is highlighted separately, including nonadjacent inventory
lines used together to establish an additional tyre set.
Structured output and field-specific evidence are checked. Drivability also requires a
capability statement matching the selected yes/no value; words such as
`körsträcka` (mileage) do not qualify.

Tyre-inclusion rules scan the description independently of model selections.
They recognize ordinary inventory lists, spaced seasonal names, `S- och V-hjul`,
`S&V-däck`, `dubbdäck`, and `friktionsdäck`. Included summer and winter sets
establish an additional set even when listed on separate lines. Offers such as
`köpa till`, `mot tillägg`, separately priced sets, and conditional paid packages
are excluded. Each seasonal offer is assessed separately; an optional winter set
does not negate included summer wheels. AWD, wheel bearings, and tyre-pressure
equipment do not establish tyre inventory. Contradictory seasonal statements and
unrecognized wording remain unknown. Shared all-season tyres do not establish two
sets. Quotes and decision reasons are retained with rule provenance; the cache
also records whether a model selection was validated, overridden, or rejected.

Explicit driving-denial rules scan the same original sentences and override a
missed or contradictory LLM selection. They recognize forms such as `Startar ej`,
`går inte att köra`, `den inte driver framåt`, reverse-only driving, a broken
gearbox, an engine that will not turn over, and mandatory towing. Historical
clauses and auxiliary-heater starting faults are excluded. A refusal to allow a
test drive alone does not establish non-drivability. The finding popup identifies
whether the decision came from a rule or the LLM; raw model selections remain
available in the cache for debugging. Extraction version eight invalidates old
findings; after restarting the app, enabled background analysis refreshes them
using the existing cached descriptions.

Swedish is analyzed directly, without a separate translation pass: translating
would add inference time and another opportunity to lose a negation, and would
not fix a narrow evidence validator. Supporting quotes always retain the original
Swedish. Ambiguous or unrecognized driving denials may still be missed, leaving
the default assumed Yes. Invalid or unsupported facts become unknown, except
drivability which defaults to Yes. A small LLM can still misread negation or seller
wording, so inspect the quotations before acting. Descriptions are treated as data, and
the model has no tools or access to application commands. Only the first 12,000 characters
of unusually long descriptions are analyzed; this is indicated in the detail page.

Results are cached by full description, model name, and extraction version in SQLite.
Changing the description text, model name, or extraction version automatically invalidates old findings. Missing descriptions
remain unclassified. Network/model errors are visible and retried after a cooldown;
collection and the website continue running. The model panel reports how many training
adverts have analyzed descriptions. These facts enter every price-model candidate as
separate yes/no/unknown features. The price model retrains after analysis batches; a
small sample of labeled drivability/extras does not guarantee an immediate accuracy gain.

Use the normal `compose.yaml` commands for upgrades, logs, and shutdown. Cached
findings are included in the tracker SQLite backup. Ollama and its model storage
are managed independently on the external server.

The previous Repair/Does not start categories are replaced by Drivable. Older cached
extractions are invalidated and automatically re-analyzed in the background. Obsolete
saved condition filters are retired with a review notice instead of silently equating
general repair needs with non-drivability. Raw sentence selections are retained in
analysis metadata for debugging future missed classifications.

### Settings

Open **Settings** in the header to select daily rescan start hours (00:00–23:00)
in **Europe/Stockholm** time. Midnight (00:00) is the default, including for older
installations with an interval setting. Select multiple hours for more frequent
rescans, or uncheck all hours to disable automatic rescans. The old `REFRESH_HOURS`
environment variable is no longer used.

The checklist persists in SQLite across restarts and applies within one minute.
Each selected hour queues all saved searches once, processed one at a time; scans
may finish after that hour. Restarting during an already queued hour does not
repeat it, and missed hours are skipped. The repeated hour when daylight saving
time ends runs once; a skipped spring hour does not run. Manual refreshes and
initial collection for new searches still queue immediately. The dashboard shows
the saved schedule; browser result polling is separate.

The page also checks whether Ollama is reachable and the configured `LLM_MODEL`
is installed, and shows whether description analysis is enabled. Use **Check
again** after starting Ollama or installing a model. Connection and analysis
configuration still use the `LLM_URL`, `LLM_MODEL`, and `LLM_ENABLED` environment
variables and require an app restart when changed.

The **Task log** groups refresh tasks under their saved search, with a link to
the search and counts of running and queued/waiting tasks. Searches with active
work appear first, and searches without retained history still appear. Renaming a
search keeps its runs together; deleted searches retain their recorded history
without a link. Startup training and saved-listing LLM backfills appear under
**Shared background work** because they have no single search owner.
Each search refresh has **Listing collection**,
**Training price model**, **LLM analysis**, and **Retraining model** underneath.
Collection includes both purchase listings and comparables. Running stages have
live timers; LLM analysis shows **completed/total done** while running or waiting,
alongside listing details and retry errors. Active analysis also shows listings
per minute and an estimated finish time after the first completed listing.
Speed uses active analysis time and excludes previously cached results; the
finish estimate includes batch pauses and retraining at the observed pace.
Expand a finished
task to see its outcome, start/end times, queue wait, and time spent in each stage.
The log refreshes every five seconds and preserves expanded entries.

Total elapsed runs from the start of collection through the final LLM/retraining
work, including waits between stages; initial queue time is separate. Stage times
accumulate actual worker calls across batches, including service and model-lock
waits. A shared LLM request or training operation can contribute to several search
tasks without running twice. Disabled LLM stages are skipped; existing valid facts
are reused. Startup training and LLM backfill for other saved listings appear as
separate system tasks. Collection and LLM workers continue independently.

Task history is stored in SQLite and starts with this version; older stage timings
cannot be reconstructed. All unfinished tasks and the latest 50 finished runs are
shown. On shutdown/restart unfinished runs become **Interrupted**, while workers
resume their normal scheduling/backfill. After an unclean stop, timings end at the
last recorded activity and are labeled accordingly, excluding unknown downtime.

### Favourites

Use the star on a result or listing page to save a car. The **Favourites** card
on the home screen collects starred listings across searches, without duplicates.
Favourites are stored in SQLite and survive refreshes, search edits and deletion;
unstar a listing to remove it. The favourites page shows last-seen timestamps,
since a saved advert may no longer be available.

Select **Favourites first** on a search page to pin matching favourites above
other results while preserving your selected sort order within each group.
This option respects all current filters and is remembered per search in this
browser. Uncheck it to return to the normal sort order.
