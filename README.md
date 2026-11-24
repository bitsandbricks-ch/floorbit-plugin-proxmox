# Proxmox VE for Floorbit

[![Test and release](https://github.com/bitsandbricks-ch/floorbit-plugin-proxmox/actions/workflows/release.yml/badge.svg?branch=main)](https://github.com/bitsandbricks-ch/floorbit-plugin-proxmox/actions/workflows/release.yml?query=branch%3Amain)

The `proxmox` plugin for [Floorbit](https://github.com/bitsandbricks-ch/floorbit): Proxmox VE hosts, VMs and containers in the inventory and on the health wall.
Install it under Settings > Plugins > Marketplace; how to set it up is in Floorbit's
[docs/plugins.md](https://github.com/bitsandbricks-ch/floorbit/blob/master/docs/plugins.md).

## Setting it up

In Proxmox, under Datacenter > Permissions > API Tokens, add a token (for example
`floorbit@pve!floorbit`) and give it the **PVEAuditor** role on `/`. It reads everything below and
can change nothing. Then set the URL (any host of the cluster, port 8006), the token ID and secret.

## What it shows

It reads `cluster/resources`, `cluster/status` and, for the inventory, each running host's
`nodes/<host>/status`.

- **Inventory:** each host (role `server`: CPU model, cores, memory, disk, Proxmox version,
  management address) and each VM and container (role `vm`: cores, memory, disk, and the host it
  runs on). Templates are left out. A stopped VM is `offline`. As with every inventory plugin, a
  hand edit wins and a sync never deletes.
- **Health wall:** each host (CPU and memory, amber from 90%, red when offline), the cluster's
  quorum (when there's more than one host), and every VM and container with the Proxmox tag
  `floorbit` (setting *Health wall tag*), red when it doesn't run. Tag the game servers and services
  the event needs.

<!-- TODO(Mike): checked against the Proxmox VE 8 API viewer, not yet against a real cluster. -->

## What is where

| File | What |
| --- | --- |
| `plugin.yaml` | The manifest: settings and what it may touch. |
| `plugin.py` | The plugin. Floorbit runs it on the local agent in the hall, which reaches Proxmox on the event network. |
| `tests/` | Tests against a fake Proxmox cluster, with Floorbit at the commit in `FLOORBIT_REF`. No network. |

## Testing

```
git clone https://github.com/bitsandbricks-ch/floorbit ../floorbit
git -C ../floorbit checkout "$(cat FLOORBIT_REF)"
pip install -r ../floorbit/requirements.txt pytest ruff
ruff check --config ../floorbit/pyproject.toml --config 'lint.isort.known-third-party=["floorbit"]' .
PYTHONPATH=../floorbit python -m pytest -q tests
```

## Releasing

Run `python scripts/changelog.py collect`, then `mason bump` (it sets the version in `plugin.yaml` and
turns `[Unreleased]` in `CHANGELOG.md` into its section), merge, then
push the tag (or `mason release tag floorbit-plugin-proxmox`):

```
git tag v1.0.0 && git push origin v1.0.0
```

The [workflow](.github/workflows/release.yml) tests it, signs `proxmox-<version>.tar.gz` with the
Floorbit plugin key (environment `plugin-signing`, secret `FLOORBIT_PLUGIN_SIGNING_KEY`) and
attaches it to the GitHub release. The Marketplace picks it up within the hour. A version is
withdrawn in [floorbit-plugins](https://github.com/bitsandbricks-ch/floorbit-plugins)' `plugins.yaml`,
never by deleting the release.
