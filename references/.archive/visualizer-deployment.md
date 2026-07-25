# Weave Visualizer — Deployment Architecture

The Weave social graph visualizer runs on DreamHost, live-connecting to the VPS database via a translation layer. No data duplication.

## Architecture

```
Browser → https://indigokarasu.com/weave/
  → index.html (D3.js force graph, static)
  → /weave/api/index.cgi (Python CGI, proxies Cypher queries)
    → http://127.0.0.1:9192 (autossh reverse tunnel)
      → VPS :9191 (ladybug_bridge.py → live weave.lbug)
```

## Components

### 1. VPS Bridge (`ladybug_bridge.py`)

- Modified to accept `--host` argument (default `127.0.0.1`)
- Bound to `0.0.0.0:9191` for external access
- Systemd service: `ladybug-bridge-weave.service`
- Firewall: iptables ACCEPT from DreamHost IP (`208.113.190.61`), DROP all others
- **Critical**: Also ACCEPT on `lo` before port rules (order matters)

```bash
iptables -I INPUT 1 -i lo -j ACCEPT
iptables -I INPUT -p tcp --dport 9191 -s 208.113.190.61 -j ACCEPT
iptables -I INPUT -p tcp --dport 9191 -j DROP
```

### 2. Reverse SSH Tunnel (autossh)

- Systemd service: `weave-tunnel.service` (enabled for reboot survival)
- VPS connects outbound to DreamHost SSH
- Forwards DreamHost `localhost:9192` → VPS `localhost:9191`
- Uses `autossh -M 0` with `ServerAliveInterval 30`
- SSH public key must be in DreamHost `~/.ssh/authorized_keys`

### 3. DreamHost CGI API (`weave-api.cgi`)

- Python CGI at `~/indigokarasu.com/weave/api/index.cgi`
- Proxies Cypher queries to `http://127.0.0.1:9192` (tunnel)
- Endpoints: `/health`, `/stats`, `/persons`, `/person/<id>`, `/relationships`, `/graph`, `/query`
- Read-only: rejects CREATE/DELETE/SET/REMOVE/MERGE/DROP
- `.htaccess` enables CGI (must use proper newlines — `scp` from file, not `echo`)

### 4. Frontend (`index.html`)

- Single-file D3.js force-directed graph from CDN
- API base: `/weave/api/index.cgi`
- Drag nodes, zoom/pan, search, click-for-details sidebar
- Dark theme, responsive

## Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| CGI returns 500 | `.htaccess` malformed | Re-upload with proper newlines |
| Bridge unreachable | Tunnel down | `systemctl restart weave-tunnel` |
| Bridge won't start | DB lock held | `systemctl kill --signal=SIGKILL` then restart |
| Local curl fails | iptables blocking lo | Add `-i lo -j ACCEPT` before port rules |
| Graph timeout | Large result | Increase `--max-time`; limited to 500 nodes |