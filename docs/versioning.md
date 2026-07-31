# Versioning

## The standard: Semantic Versioning (SemVer)

`MAJOR.MINOR.PATCH` — e.g. `1.0.0`. Bump rules:

- **PATCH** (`1.0.0` → `1.0.1`) — bug fixes, internal refactors, anything
  where behavior for whoever's using the app doesn't change on purpose.
- **MINOR** (`1.0.0` → `1.1.0`) — new features, new pages/endpoints,
  anything additive that doesn't break what already worked.
- **MAJOR** (`1.0.0` → `2.0.0`) — breaking changes: something that requires
  manual intervention to keep working (a required migration, a
  renamed/removed env var with no default, a removed feature, a change
  someone has to act on rather than just receiving).

This is the actual consensus standard ([semver.org](https://semver.org)),
not a convention specific to published libraries — it applies the same way
to an app you deploy yourself.

For a library, "breaking" means "code that imports this will crash." For
an app with one deployer (you), "breaking" means: does deploying this
require doing something by hand beyond `git pull` + restart — a
migration, a new env var, manually fixing data. If yes, that's
MAJOR-worthy regardless of how small the code diff looks. Additive
functionality behind the same deploy process is MINOR. Pure fixes/cleanup
is PATCH.

Major version `0` (where this project started) means "anything can
change, no stability promised yet" — the meaningful milestone isn't a
number, it's the moment you'd say "I don't want to break this carelessly
anymore." This project made that call and moved to `1.0.0`.

## Where the version lives, and what enforces it

The version is declared once, in the root `pyproject.toml`'s
`[project] version` field — one number for the whole app (frontend and
backend ship as a single Docker image, built by `deploy/Dockerfile`), not
a separate one per package. `web/package.json`'s own `version` field is
unused boilerplate (`"private": true`, never published) and isn't part of
this scheme.

`.github/workflows/backend.yml` enforces the process end to end:

- **`version-check`** (on every PR): fails unless the PR's version is
  strictly greater than `main`'s — catches both "forgot to bump" and
  "accidentally reverted someone else's bump."
- **`tag`** (on every push to `main`, after tests pass): reads the version
  and creates + pushes a matching `vX.Y.Z` git tag, if one doesn't already
  exist for it — so a tag always corresponds to an exact commit that
  actually landed on `main`, never something created by hand out of sync.

Mechanically: bump the version in the same PR as the change it describes,
one bump per merge (not per commit) — the version tracks what shipped, not
how many commits it took to get there.

## The Docker image is tagged with the same version

`deploy/deploy.sh`/`deploy/deploy-staging.sh` read the version out of
`pyproject.toml` on the VM (after `git pull`, so it reflects whatever
commit is actually being deployed) and pass it to `docker compose` as
`APP_VERSION`, which `deploy/docker-compose.yml`/
`deploy/docker-compose.staging.yml` use to tag the built image
(`financedashboard-api:1.0.0`, `financedashboard-api-staging:1.0.0`).

This closes the gap where "what's running in prod" only existed in
`git log` on the server — `docker images` (or `docker inspect` on the
running container) now shows exactly which version is live, traceable
straight back to the git tag and the PR that shipped it. Useful the day
something breaks and you need to identify (or roll back to) a known-good
version instead of guessing which commit was live.

One consequence worth knowing: `docker image prune -f` (run at the end of
every deploy) only removes *dangling* (untagged) images. Before version
tagging, every rebuild reused the same implicit tag, so the previous build
became dangling and got cleaned up automatically. Now every deployed
version gets its own real tag, so old versions accumulate on disk instead
of being pruned automatically — fine for a while, but worth manually
clearing out old tagged images (`docker image prune -a` app cache, or
`docker rmi` specific old versions) if disk ever gets tight on the VM.
