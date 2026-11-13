# Contributing

Keep the bridge local and read-only. Any serial command additions must be
explicitly reviewed and covered by tests; MQTT control topics are outside the
scope of this project.

Use Python 3.12 and install `requirements.txt` in a virtual environment. Run the
unit and Docker integration commands in [README.md](README.md) before submitting
a pull request. Protocol fixtures should cover incomplete and invalid replies
as well as successful decoding.

For compatibility reports, include the inverter model, firmware/protocol identity,
bridge logs, and sanitized `--probe` output. Remove credentials, private broker
addresses, serial numbers, and any other identifying data. Do not submit `.env`.
Distinguish real-hardware results from simulated test results.

The files under `vendor/` are pinned upstream copies. Preserve their license and
record the source revision in `vendor/UPSTREAM.md` when updating them.

Check the **Security scans** workflow as well as the integration tests. Review
vulnerability advisories and available fixes before changing a dependency or the
base image. Keep the Python audit tool version and action commit pins current. Do not suppress
findings without documenting why they do not apply to this project.

The image publisher reuses the integration workflow before pushing AMD64 and
ARM64 images to GHCR. Dependabot update pull requests require review and merge
before runtime dependency changes are published. See `docs/containers.md` for
rebuild schedules, tags, and package permissions.
