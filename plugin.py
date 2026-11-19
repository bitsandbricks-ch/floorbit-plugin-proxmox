"""Proxmox VE hosts, VMs and containers for Floorbit's inventory and health wall.

Reads the API with a token (``Authorization: PVEAPIToken=user@realm!name=secret``):
``cluster/resources`` (every host, VM and container with its size and state),
``cluster/status`` (quorum and each host's address) and, per running host, ``nodes/<host>/status``
(CPU model and Proxmox version). The PVEAuditor role reads all of it and changes nothing.

Floorbit runs this on the local agent in the hall.
"""
from urllib.parse import quote

from floorbit.plugins.base import Plugin as Base
from floorbit.plugins.base import PluginError

API = '/api2/json'
BUSY = 90                    # % CPU or memory at which a host turns amber
MAX_WATCHED = 40             # the health wall takes 50 checks per plugin; hosts and the cluster need some
MB, GB = 1024 ** 2, 1024 ** 3


def _int(value):
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _size(value, unit):
    value = _int(value)
    return round(value / unit) if value and value > 0 else None


def _pct(part, whole):
    part, whole = _num(part), _num(whole)
    return round(part * 100 / whole, 1) if part is not None and whole else None


def _num(value):
    return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else None


def _tags(value):
    return {t.strip().lower() for t in str(value or '').replace(',', ';').replace(' ', ';').split(';') if t.strip()}


class Plugin(Base):
    def _http(self):
        url, token, secret = (self.settings.get(k) for k in ('url', 'token_id', 'token_secret'))
        if not (url and token and secret):
            raise PluginError('Set the Proxmox URL, API token ID and secret under Settings > Plugins first.')
        return self.ctx.http(url, timeout=15, headers={'Authorization': f'PVEAPIToken={token}={secret}'})

    def _get(self, http, path):
        resp = http.get(API + path, headers={'Accept': 'application/json'})
        if resp.status_code == 401:
            raise PluginError('Proxmox refused the API token.')
        if resp.status_code == 403:
            raise PluginError('The API token may not read this. Give it the PVEAuditor role on /.')
        if resp.status_code >= 400:
            raise PluginError(f'Proxmox answered {resp.status_code}.')
        try:
            body = resp.json()
        except ValueError as exc:
            raise PluginError("Proxmox's answer isn't JSON. Is the URL a Proxmox host with its port (8006)?") from exc
        return body.get('data') if isinstance(body, dict) else None

    def _read(self, details=True):
        http = self._http()
        resources = [r for r in self._get(http, '/cluster/resources') or [] if isinstance(r, dict)]
        status = [s for s in self._get(http, '/cluster/status') or [] if isinstance(s, dict)]
        nodes = [r for r in resources if r.get('type') == 'node' and r.get('node')]
        guests = [r for r in resources if r.get('type') in ('qemu', 'lxc') and not r.get('template')
                  and _int(r.get('vmid')) is not None]
        ips = {s.get('name'): s.get('ip') for s in status if s.get('type') == 'node'}
        cluster = next((s for s in status if s.get('type') == 'cluster'), None)
        info = {}
        if details:
            for n in nodes:
                if n.get('status') == 'online':
                    try:
                        info[n['node']] = self._get(http, f"/nodes/{quote(str(n['node']), safe='')}/status") or {}
                    except PluginError:
                        info[n['node']] = {}       # one host's details are worth less than the whole list
        return nodes, guests, ips, cluster, info

    # Floorbit's areas ------------------------------------------------------------------------
    def inventory(self):
        nodes, guests, ips, _cluster, info = self._read()
        out = []
        for n in nodes:
            name = str(n['node'])
            details = info.get(name) or {}
            version = str(details.get('pveversion') or '').split('/')
            device = {'id': f'node/{name}', 'name': name, 'role': 'server', 'platform': 'proxmox',
                      'status': 'active' if n.get('status') == 'online' else 'offline',
                      'os': f'Proxmox VE {version[1]}' if len(version) > 1 else 'Proxmox VE',
                      'cpu': str((details.get('cpuinfo') or {}).get('model') or ''),
                      'cores': _int(n.get('maxcpu')), 'memory_mb': _size(n.get('maxmem'), MB),
                      'disk_gb': _size(n.get('maxdisk'), GB), 'mgmt_ip': ips.get(name)}
            out.append({k: v for k, v in device.items() if v not in ('', None)})
        for g in guests:
            kind = g['type']
            device = {'id': f"{kind}/{g['vmid']}", 'name': str(g.get('name') or f"{kind}-{g['vmid']}"),
                      'role': 'vm', 'platform': 'lxc' if kind == 'lxc' else 'qemu', 'host': g.get('node'),
                      'status': 'active' if g.get('status') == 'running' else 'offline',
                      'cores': _int(g.get('maxcpu')), 'memory_mb': _size(g.get('maxmem'), MB),
                      'disk_gb': _size(g.get('maxdisk'), GB)}
            out.append({k: v for k, v in device.items() if v not in ('', None)})
        return out

    def health_checks(self):
        nodes, guests, _ips, cluster, _info = self._read(details=False)
        checks = []
        if cluster is not None and len(nodes) > 1:
            quorate = bool(cluster.get('quorate'))
            online = sum(n.get('status') == 'online' for n in nodes)
            checks.append({'id': 'cluster', 'name': f"Proxmox {cluster.get('name') or 'cluster'}", 'kind': 'other',
                           'status': 'up' if quorate and online == len(nodes) else 'warn' if quorate else 'down',
                           'summary': f'{online} of {len(nodes)} hosts online' + ('' if quorate else ', no quorum')})
        for n in nodes:
            up = n.get('status') == 'online'
            cpu = round(_num(n.get('cpu')) * 100, 1) if up and _num(n.get('cpu')) is not None else None
            mem = _pct(n.get('mem'), n.get('maxmem')) if up else None
            busy = any(v is not None and v >= BUSY for v in (cpu, mem))
            running = sum(g.get('node') == n['node'] and g.get('status') == 'running' for g in guests)
            metrics = [{'label': 'CPU', 'value': cpu, 'unit': '%', 'tone': 'warn' if cpu and cpu >= BUSY else ''},
                       {'label': 'Memory', 'value': mem, 'unit': '%', 'tone': 'warn' if mem and mem >= BUSY else ''},
                       {'label': 'Running', 'value': running}]
            checks.append({'id': f"node-{n['node']}", 'name': str(n['node']), 'kind': 'other',
                           'status': 'down' if not up else 'warn' if busy else 'up',
                           'summary': f'{running} VMs and containers running' if up else 'offline',
                           'metrics': [m for m in metrics if m['value'] is not None]})
        tag = str(self.settings.get('watch_tag') or '').strip().lower()
        watched = [g for g in guests if tag and tag in _tags(g.get('tags'))][:MAX_WATCHED]
        for g in sorted(watched, key=lambda g: str(g.get('name') or '').lower()):
            state = str(g.get('status') or 'unknown')
            checks.append({'id': f"{g['type']}-{g['vmid']}", 'name': str(g.get('name') or g['vmid']),
                           'kind': 'other', 'status': 'up' if state == 'running' else 'down',
                           'summary': f"{state} on {g.get('node')}"})
        return checks

    def test(self):
        nodes, guests, _ips, _cluster, _info = self._read(details=False)
        running = sum(g.get('status') == 'running' for g in guests)
        return f'Connected: {len(nodes)} hosts, {len(guests)} VMs and containers ({running} running).'
