# Changelog

Each release has a section; its text becomes the GitHub release notes and the Marketplace's
"what's new". New entries are fragments in `changelog.d/`: `python scripts/changelog.py collect`
adds them under `## [Unreleased]`, and `mason bump` turns that into the version.

## 1.0.0

- First release: Proxmox VE hosts, VMs and containers in the inventory; hosts, the cluster's quorum
  and tagged VMs on the health wall.
