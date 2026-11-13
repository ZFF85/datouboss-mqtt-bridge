# Container images and updates

## Registry and platforms

Images are published to the [GitHub Container Registry package](https://github.com/users/ZFF85/packages/container/package/datouboss-mqtt-bridge):

```text
ghcr.io/zff85/datouboss-mqtt-bridge
```

Each published image contains `linux/amd64` and `linux/arm64` variants. ARM64
supports hosts running a 64-bit ARM operating system, such as a Raspberry Pi
with a 64-bit Linux installation. A 32-bit ARM variant is not built.

## Deploy with Compose

Copy `compose.image.yaml` and `.env.example` into a deployment directory, then
copy `.env.example` to `.env` and configure the serial adapter and MQTT broker.
A complete Git checkout is optional for this deployment.

```bash
docker compose -f compose.image.yaml pull
docker compose -f compose.image.yaml run --rm datouboss-mqtt-bridge python bridge.py --probe
docker compose -f compose.image.yaml up -d
docker compose -f compose.image.yaml logs -f --tail=50
```

Stop any existing bridge before running `--probe`, as only one process can use
the serial adapter. `SERIAL_DEVICE` selects the host adapter, which is mapped to
`/dev/inverter` in the container. Your `.env` is supplied only at runtime and is
excluded from image builds.

## Image tags

| Tag | Meaning |
| --- | --- |
| `latest`, `main` | Latest successful publication from `main`, including scheduled rebuilds. |
| `build-<run-id>-<attempt>` | A particular workflow run and attempt, useful for rollback. |
| `sha-<full-commit>` | Source commit, republished if that commit is rebuilt with a newer base image. |
| `1.2.3`, `1.2` | Created when a matching Git tag such as `v1.2.3` is pushed. |

`latest`, `main`, source-commit tags, and release aliases may move when rebuilt.
For exact image contents, pin the registry digest shown in the publication
workflow summary. A source commit identifies code, but not the base-image
contents of every later rebuild.

Set an optional override in `.env`, for example:

```dotenv
INVERTER_IMAGE=ghcr.io/zff85/datouboss-mqtt-bridge:1.2.3
```

This release tag is an example and is available only after the corresponding
Git tag has been published. To pin exact contents, replace the example digest
below with a real digest from a successful build:

```dotenv
INVERTER_IMAGE=ghcr.io/zff85/datouboss-mqtt-bridge@sha256:REPLACE_WITH_PUBLISHED_DIGEST
```

## Updating and rollback

After a new image has been published:

```bash
docker compose -f compose.image.yaml pull
docker compose -f compose.image.yaml up -d
docker compose -f compose.image.yaml logs --tail=50
```

`pull_policy: always` also checks the registry when Compose starts a service.
Publishing an image does not automatically restart your running installation.
No unattended updater is installed on your host.

For rollback, set `INVERTER_IMAGE` to a previous build tag or digest, then repeat
the `pull` and `up -d` commands. Keep `.env` and the serial-device configuration
when changing images.

## Automated publication

The **Publish container image** workflow runs on:

- Pushes to `main`.
- Version tag pushes matching `v*`, such as `v1.2.3`.
- Manual runs from the Actions page on `main` or a version tag.
- A weekly schedule, every Monday at 09:17 UTC, using the default branch.

The workflow first runs the hardware-free integration tests, then builds and
publishes both platforms. A failing test prevents publication. It uses the
repository's short-lived `GITHUB_TOKEN` with `packages: write`, so no personal
registry token needs to be stored in repository secrets. OCI source labels link
the package to its GitHub repository.

Every build pulls the current base image. Weekly builds also disable the build
cache, allowing newly published base-image contents to be included without a
source-code change. Updates are picked up when upstream publishes a new image
under the referenced base tag, rather than immediately when an individual OS
package is released.

Dependabot checks Python requirements, the Dockerfile base-image version, and
GitHub Actions every Monday. It opens update pull requests, which run the tests
and security checks. Merging a dependency update into `main` triggers a new image
build. Updates are not automatically merged.

The separate **Security scans** workflow reports known Python dependency
vulnerabilities. Container OS packages are not scanned. It can fail while publication succeeds, because publication is
gated on integration tests rather than vulnerability findings. Inspect those
reports before choosing an image for your deployment.

## Initial GitHub setup

After the first successful publication, check the package's visibility. GitHub
creates new packages as private by default, even for a public source repository.
For anonymous image pulls, open the package page, choose **Package settings →
Change visibility → Public**, and confirm the package name. Keep it private if
you want image downloads restricted to authenticated users.

For a private package, authenticate Docker with a GitHub personal access token
(classic) with `read:packages`, using `docker login ghcr.io`, before running the
Compose commands. Public packages can be pulled without logging in.

If publication is denied, check that repository Actions are enabled and that the
workflow can write packages. An existing package must allow this repository's
Actions to publish to it. See GitHub's [Container registry documentation](https://docs.github.com/en/packages/working-with-a-github-packages-registry/working-with-the-container-registry).
