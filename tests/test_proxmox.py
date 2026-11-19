"""Proxmox VE against a fake cluster, never a real one."""
import pytest
import requests
from floorbit import health, inventory
from floorbit.plugins.base import PluginError

GB, MB = 1024 ** 3, 1024 ** 2
RESOURCES = [
    {"id": "node/pve1", "type": "node", "node": "pve1", "status": "online", "maxcpu": 16, "cpu": 0.25,
     "maxmem": 64 * GB, "mem": 60 * GB, "maxdisk": 100 * GB},
    {"id": "node/pve2", "type": "node", "node": "pve2", "status": "offline", "maxcpu": 8, "maxmem": 32 * GB},
    {"id": "qemu/100", "type": "qemu", "vmid": 100, "name": "cs2-01", "node": "pve1", "status": "running",
     "maxcpu": 4, "maxmem": 8 * GB, "maxdisk": 60 * GB, "tags": "floorbit;game"},
    {"id": "lxc/101", "type": "lxc", "vmid": 101, "name": "dns-01", "node": "pve1", "status": "stopped",
     "maxcpu": 1, "maxmem": 512 * MB, "maxdisk": 8 * GB, "tags": "Floorbit"},
    {"id": "qemu/102", "type": "qemu", "vmid": 102, "node": "pve1", "status": "running", "maxcpu": 2},
    {"id": "qemu/9000", "type": "qemu", "vmid": 9000, "name": "debian-template", "node": "pve1",
     "status": "stopped", "template": 1},
    {"id": "storage/pve1/local", "type": "storage", "node": "pve1", "storage": "local"},
]
STATUS = [{"type": "cluster", "name": "hall", "quorate": 1, "nodes": 2},
          {"type": "node", "name": "pve1", "ip": "192.0.2.11", "online": 1},
          {"type": "node", "name": "pve2", "ip": "192.0.2.12", "online": 0}]
NODE = {"cpuinfo": {"model": "AMD EPYC 7302P 16-Core Processor", "cpus": 16},
        "pveversion": "pve-manager/8.2.4/faa83925c9641325"}


class Answer:
    def __init__(self, body=None, status=200):
        self.status_code = status
        self._body = body

    def json(self):
        if isinstance(self._body, str):
            raise ValueError("not JSON")
        return self._body


@pytest.fixture
def pve(monkeypatch):
    seen = []

    def fake(self, method, url, **kw):
        path = url.removeprefix("https://pve.example.com:8006/api2/json")
        seen.append(path)
        if self.headers.get("Authorization") != "PVEAPIToken=floorbit@pve!floorbit=example-secret":
            return Answer({"data": None}, 401)
        data = {"/cluster/resources": RESOURCES, "/cluster/status": STATUS, "/nodes/pve1/status": NODE}.get(path)
        return Answer({"data": data}) if data is not None else Answer({"data": None}, 500)

    monkeypatch.setattr(requests.Session, "request", fake)
    return seen


class Spec:
    id, name = "proxmox", "Proxmox VE"


def plugin(make, **extra):
    return make(**{"url": "https://pve.example.com:8006", "token_id": "floorbit@pve!floorbit",
                   "token_secret": "example-secret", **extra})


def test_inventory(make, pve):
    items, skipped = inventory.clean_feed(plugin(make).inventory())
    assert skipped == 0
    by = {i["ref"]: i for i in items}
    assert set(by) == {"node/pve1", "node/pve2", "qemu/100", "lxc/101", "qemu/102"}   # no template
    pve1 = by["node/pve1"]
    assert pve1["role"] == "server" and pve1["os"] == "Proxmox VE 8.2.4" and pve1["mgmt_ip"] == "192.0.2.11"
    assert pve1["cpu"] == "AMD EPYC 7302P 16-Core Processor" and pve1["status"] == "active"
    assert by["node/pve2"]["status"] == "offline" and "cpu" not in by["node/pve2"]
    cs2 = by["qemu/100"]
    assert (cs2["name"], cs2["role"], cs2["host"], cs2["platform"], cs2["status"]) == \
        ("cs2-01", "vm", "pve1", "qemu", "active")
    assert by["lxc/101"]["platform"] == "lxc" and by["lxc/101"]["status"] == "offline"
    assert by["qemu/102"]["name"] == "qemu-102"
    assert "/nodes/pve2/status" not in pve          # offline hosts aren't asked


def test_inventory_sizes(make, pve):
    by = {i["id"]: i for i in plugin(make).inventory()}
    assert (by["node/pve1"]["cores"], by["node/pve1"]["memory_mb"], by["node/pve1"]["disk_gb"]) == (16, 65536, 100)
    assert (by["lxc/101"]["memory_mb"], by["lxc/101"]["disk_gb"]) == (512, 8)


def test_health_wall(make, pve):
    checks = {c["id"]: c for c in health.clean(Spec, plugin(make).health_checks())}
    assert checks["proxmox:cluster"]["status"] == "warn"
    assert checks["proxmox:cluster"]["summary"] == "1 of 2 hosts online"
    pve1 = checks["proxmox:node-pve1"]
    assert pve1["status"] == "warn"               # memory 93.8%
    assert {m["label"]: m["value"] for m in pve1["metrics"]} == {"CPU": 25.0, "Memory": 93.8, "Running": 2}
    assert checks["proxmox:node-pve2"]["status"] == "down"
    assert checks["proxmox:qemu-100"]["status"] == "up"
    assert checks["proxmox:lxc-101"]["status"] == "down" and checks["proxmox:lxc-101"]["summary"] == "stopped on pve1"
    assert "proxmox:qemu-102" not in checks       # not tagged
    assert not [p for p in pve if p.startswith("/nodes/")]
    assert not [c for c in plugin(make, watch_tag="").health_checks() if c["id"].startswith(("qemu", "lxc"))]


def test_test_button_and_errors(make, pve):
    assert plugin(make).test() == "Connected: 2 hosts, 3 VMs and containers (2 running)."
    with pytest.raises(PluginError, match="API token ID and secret"):
        make(url="https://pve.example.com:8006").test()
    with pytest.raises(PluginError, match="refused the API token"):
        plugin(make, token_secret="wrong").test()
