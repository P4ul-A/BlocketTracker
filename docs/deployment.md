# HexOS / Dockhand deployment

## Prepare storage and identity

Create a dedicated dataset/directory for tracker data using your NAS storage tools.
Use the actual host path, for example `/mnt/tank/app-data/blocket-tracker`; pool
names and dataset layouts vary. Do not use that example literally unless it exists.
Keep the directory local to the NAS rather than mounting SQLite over SMB/NFS.

Set `DATA_PATH` in `.env` to that existing absolute directory. Select a numeric
UID/GID with read/write/traverse access to the directory and its parents, and use
those numbers as `PUID` and `PGID`. The image's default user is `10001:10001`; Compose
also supports a different numeric identity. Use the dataset ACL controls to grant
access. There is no need to run the container as root or grant broad world-write access.

The bind mount deliberately refuses to create a missing host directory. A typo in
`DATA_PATH` should stop deployment rather than silently put your database elsewhere.
The mount is `/data` inside the container. The root filesystem is read-only, with
a writable temporary directory and data mount.

## Prepare the stack

Obtain this repository on the NAS (clone your Git remote or copy the source).
Dockhand needs the complete source directory as build context: `Dockerfile`,
`requirements.txt`, and `app/`, as well as `compose.yaml`. Keep `.env` alongside the
Compose file, or supply its values through the stack's environment configuration.
Copying only the Compose text into a new directory does not supply the build context.

Build and deploy the Compose stack using Dockhand. If its selected workflow does
not offer a local build, build the named image on the Docker host first:

```sh
docker compose config
docker compose build
docker compose up -d
```

The default host port is `8080`; change `PORT` if it conflicts with another service.
Open `http://NAS-LAN-IP:8080`. The container exposes port `8000` internally. This
application is separate from the HexOS administration UI and requires no changes
to its ports. Keep access on LAN/VPN; the app does not provide authentication.

The image targets Linux AMD64 through Compose and uses CPU-only numeric libraries.
Always run one replica and one Uvicorn worker so only one scheduler owns collection.

## First refresh

Create a named search such as `VOLVO` / `V60`. Exact model matching is intentional.
The first refresh is queued automatically. It may take several minutes, depending
on the number of pages and detail requests. Leave the search page open to see status
updates every ten seconds. Refreshing the browser does not restart collection.

At least 100 usable adverts overall and 20 exact make/model comparables are needed
for an estimate. A page-cap warning may appear for popular cars. Tightening purchase
filters does not restrict the broader model sample. Set `MAX_PAGES` lower only if you
accept reduced coverage; lowering `DETAIL_LIMIT` reduces detail work, not summary
pagination. Set `GEOCODING=false` to show only available advert coordinates.

## Observe and troubleshoot

Use Dockhand's container logs and health status, or:

```sh
docker compose ps
docker compose logs --tail=100 blocket-tracker
```

- Permission errors or startup failure: check `DATA_PATH`, parent traversal, dataset
  ACLs, and configured UID/GID. Existing SQLite/WAL files must also be writable.
- Upstream `429`, network errors, or schema failures: inspect the search status;
  bounded retries happen automatically. Last observations remain stored.
- Empty results: check the exact model spelling, saved seller/fuel filters, and
  collection status. Fuel labels come from Swedish adverts.
- No estimates: inspect the model sample and ensure adverts have year/mileage.
- “Not seen” records: absent from a fully completed search; no sale is inferred.
- Container unhealthy: examine `/health` and logs. A database permission problem
  can prevent startup; an upstream API outage alone does not fail container health.

## Backup and restore

All persistent application state is in the data directory, including
`tracker.sqlite3`, `tracker.sqlite3-wal`, and `tracker.sqlite3-shm` when present.
For a simple consistent backup, stop the service before copying/snapshotting the
whole dataset directory. Do not copy only the main SQLite file while it is running.

```sh
docker compose stop blocket-tracker
# Snapshot or copy DATA_PATH with your NAS backup tools now.
docker compose start blocket-tracker
```

To restore: stop the service, restore the full saved data directory, check its
ownership/ACLs against `PUID`/`PGID`, then start the service. Keep a copy of `.env`
and the corresponding source version separately. Dataset snapshots alone do not
protect against loss of the NAS; keep a separate backup if needed.

## Upgrade

Back up the dataset and retain the previous source revision first. Update the
repository, rebuild, and recreate through Dockhand (or `docker compose up -d --build`).
The bind-mounted database survives container replacement. Schema version two converts existing stored mileage and saved search bounds from
Swedish mil to kilometres once, without changing prices or raw upstream responses.
Its migration and version marker commit together; subsequent starts do not repeat it.
The model mileage scale changes proportionally, preserving existing estimates. A restart during
collection queues an interrupted search for retry. Do not run an older application revision against the converted database; restore
the matching backup when rolling back.

## External Ollama description analysis

No Ollama service is deployed by the tracker. For local testing with Docker Desktop
and Ollama installed on the Mac, defaults use `LLM_ENABLED=true`,
`LLM_URL=http://host.docker.internal:11434`, and `LLM_MODEL=qwen3:1.7b`.
Run `ollama pull qwen3:1.7b` on the Mac once and start its Ollama service.

Configure `LLM_ENABLED`, `LLM_URL`, `LLM_MODEL`, `LLM_TIMEOUT`, and `LLM_THREADS`
in `.env` or Dockhand's environment. Keep using only `compose.yaml`. Recreate the
tracker after changing these values. Native Python uses `http://localhost:11434`
as its default endpoint and requires `LLM_ENABLED=true` to be exported.

For NAS deployment, use the external Ollama server's reachable IP and port, e.g.
`http://192.168.1.50:11434`. If it runs on the NAS itself, the service must still
be reachable from Docker; `localhost` inside the tracker refers to the tracker.
Use the host's reachable LAN IP or configure a host-gateway alias on Linux.

Existing and new descriptions are analyzed automatically. The search page shows
progress and retries; collection continues through an Ollama outage. Only available
descriptions can be analyzed, so the detail collection budget affects coverage.
Cached findings are in the SQLite dataset backup. External model weights and the
Ollama service are managed separately.
