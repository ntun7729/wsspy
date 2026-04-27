# wsspy

Pure Python launcher and reverse proxy for Xray VLESS over WebSocket.

No Dockerfile is used. The Python app downloads the latest Xray release at runtime, writes the Xray config, starts Xray as a background subprocess, serves a normal website at `/`, and raw-proxies WebSocket upgrade traffic on `/p7v4n9x2` to Xray.

## Run

```bash
python3 server.py
```

The app listens on port `8080` by default:

```bash
curl http://127.0.0.1:8080/
```

## Client values

- Protocol: `vless`
- UUID: `5d3f6a0c-8c6e-43cc-92f5-7fb33c5d97e8`
- Encryption: `none`
- Transport: `ws`
- WebSocket path: `/p7v4n9x2`
- Internal Xray listener: `127.0.0.1:10000`

If your hosting provider terminates HTTPS/TLS before the Python app, use TLS in the client and keep Xray security as `none`.

## Environment variables

| Variable | Default | Description |
| --- | --- | --- |
| `HOST` | `0.0.0.0` | Python HTTP bind address |
| `PORT` | `8080` | Python HTTP port |
| `WS_PATH` | `/p7v4n9x2` | WebSocket path exposed publicly |
| `XRAY_UUID` | `5d3f6a0c-8c6e-43cc-92f5-7fb33c5d97e8` | VLESS client UUID |
| `XRAY_HOST` | `127.0.0.1` | Xray bind address |
| `XRAY_PORT` | `10000` | Xray inbound port |
| `XRAY_DIR` | `.xray` | Runtime folder for Xray binary/config |
| `DEBUG` | unset | Set to `1` to keep Python stdout/stderr visible |

Example:

```bash
PORT=8000 WS_PATH=/mysecret XRAY_UUID=00000000-0000-0000-0000-000000000000 python3 server.py
```

## Logs

Logs are intentionally hidden:

- Python stdout/stderr are redirected to `/dev/null` unless `DEBUG=1`.
- Xray stdout/stderr are redirected to `/dev/null`.
- Xray config uses `access: "none"`, `error: "none"`, and `loglevel: "none"`.

## Notes

This app requires a Linux `amd64` or `arm64` runtime that allows outbound downloads, `chmod +x`, subprocess execution, long-running processes, and WebSocket upgrade traffic.
