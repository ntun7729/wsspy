#!/usr/bin/env python3
import asyncio
import json
import mimetypes
import os
import platform
import signal
import stat
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
PUBLIC_DIR = BASE_DIR / "public"
XRAY_DIR = Path(os.environ.get("XRAY_DIR", BASE_DIR / ".xray")).resolve()
XRAY_BIN = XRAY_DIR / ("xray.exe" if platform.system().lower() == "windows" else "xray")
XRAY_CONFIG = XRAY_DIR / "config.json"

HOST = os.environ.get("HOST", "0.0.0.0")
PORT = int(os.environ.get("PORT", "8080"))
XRAY_HOST = os.environ.get("XRAY_HOST", "127.0.0.1")
XRAY_PORT = int(os.environ.get("XRAY_PORT", "10000"))
WS_PATH = os.environ.get("WS_PATH", "/p7v4n9x2")
XRAY_UUID = os.environ.get("XRAY_UUID", "5d3f6a0c-8c6e-43cc-92f5-7fb33c5d97e8")

MAX_HEADER_BYTES = 65536
BUFFER_SIZE = 65536

if not WS_PATH.startswith("/"):
    WS_PATH = "/" + WS_PATH

# Hide Python-level stdout/stderr unless explicitly debugging.
if os.environ.get("DEBUG") != "1":
    sys.stdout = open(os.devnull, "w")
    sys.stderr = open(os.devnull, "w")

xray_process: subprocess.Popen | None = None


def _urlopen(url: str, timeout: int = 60):
    req = urllib.request.Request(url, headers={"User-Agent": "wsspy/1.0"})
    return urllib.request.urlopen(req, timeout=timeout)


def _xray_asset_name() -> str:
    system = platform.system().lower()
    machine = platform.machine().lower()

    if system != "linux":
        raise RuntimeError(f"unsupported OS for automatic Xray download: {system}")

    if machine in {"x86_64", "amd64"}:
        return "Xray-linux-64.zip"
    if machine in {"aarch64", "arm64", "armv8", "arm64-v8a"}:
        return "Xray-linux-arm64-v8a.zip"

    raise RuntimeError(f"unsupported CPU architecture for automatic Xray download: {machine}")


def ensure_xray_binary() -> None:
    if XRAY_BIN.exists():
        XRAY_BIN.chmod(XRAY_BIN.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
        return

    XRAY_DIR.mkdir(parents=True, exist_ok=True)
    asset_name = _xray_asset_name()

    with _urlopen("https://api.github.com/repos/XTLS/Xray-core/releases/latest", timeout=30) as response:
        release = json.loads(response.read().decode("utf-8"))

    asset_url = None
    for asset in release.get("assets", []):
        if asset.get("name") == asset_name:
            asset_url = asset.get("browser_download_url")
            break

    if not asset_url:
        raise RuntimeError(f"could not find Xray release asset: {asset_name}")

    archive_path = XRAY_DIR / asset_name
    with _urlopen(asset_url, timeout=120) as response:
        archive_path.write_bytes(response.read())

    with zipfile.ZipFile(archive_path) as archive:
        for member in archive.namelist():
            filename = Path(member).name
            if filename in {"xray", "geoip.dat", "geosite.dat"}:
                target = XRAY_DIR / filename
                with archive.open(member) as src, target.open("wb") as dst:
                    dst.write(src.read())

    archive_path.unlink(missing_ok=True)

    if not XRAY_BIN.exists():
        raise RuntimeError("Xray binary was not found after extraction")

    XRAY_BIN.chmod(XRAY_BIN.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)


def write_xray_config() -> None:
    XRAY_DIR.mkdir(parents=True, exist_ok=True)
    config = {
        "log": {
            "access": "none",
            "error": "none",
            "loglevel": "none",
            "dnsLog": False,
        },
        "inbounds": [
            {
                "tag": "vless-ws-in",
                "listen": XRAY_HOST,
                "port": XRAY_PORT,
                "protocol": "vless",
                "settings": {
                    "clients": [
                        {
                            "id": XRAY_UUID,
                            "level": 0,
                            "email": "wsspy@local",
                        }
                    ],
                    "decryption": "none",
                },
                "streamSettings": {
                    "network": "ws",
                    "security": "none",
                    "wsSettings": {
                        "path": WS_PATH,
                    },
                },
            }
        ],
        "outbounds": [
            {
                "tag": "direct",
                "protocol": "freedom",
            },
            {
                "tag": "blocked",
                "protocol": "blackhole",
            },
        ],
    }
    XRAY_CONFIG.write_text(json.dumps(config, indent=2), encoding="utf-8")


def start_xray() -> None:
    global xray_process
    ensure_xray_binary()
    write_xray_config()

    env = os.environ.copy()
    env["XRAY_LOCATION_ASSET"] = str(XRAY_DIR)

    xray_process = subprocess.Popen(
        [str(XRAY_BIN), "run", "-config", str(XRAY_CONFIG)],
        cwd=str(XRAY_DIR),
        env=env,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        close_fds=True,
    )


def stop_xray() -> None:
    if not xray_process:
        return
    if xray_process.poll() is None:
        xray_process.terminate()
        try:
            xray_process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            xray_process.kill()


async def wait_for_xray() -> None:
    deadline = asyncio.get_running_loop().time() + 15
    while asyncio.get_running_loop().time() < deadline:
        if xray_process and xray_process.poll() is not None:
            raise RuntimeError("Xray exited during startup")
        try:
            reader, writer = await asyncio.open_connection(XRAY_HOST, XRAY_PORT)
            writer.close()
            await writer.wait_closed()
            return
        except OSError:
            await asyncio.sleep(0.2)
    raise RuntimeError("Xray did not become ready")


async def read_http_header(reader: asyncio.StreamReader) -> bytes:
    data = bytearray()
    while b"\r\n\r\n" not in data:
        chunk = await reader.read(4096)
        if not chunk:
            break
        data.extend(chunk)
        if len(data) > MAX_HEADER_BYTES:
            raise ValueError("request header too large")
    return bytes(data)


def parse_request(header_data: bytes) -> tuple[str, str, str, dict[str, str]]:
    header_end = header_data.find(b"\r\n\r\n")
    header_bytes = header_data if header_end == -1 else header_data[:header_end]
    lines = header_bytes.decode("iso-8859-1", errors="replace").split("\r\n")
    if not lines or len(lines[0].split()) != 3:
        raise ValueError("bad request line")
    method, target, version = lines[0].split()
    headers: dict[str, str] = {}
    for line in lines[1:]:
        if ":" in line:
            key, value = line.split(":", 1)
            headers[key.strip().lower()] = value.strip()
    path = urllib.parse.urlsplit(target).path or "/"
    return method, path, version, headers


def is_ws_upgrade(headers: dict[str, str]) -> bool:
    upgrade = headers.get("upgrade", "").lower()
    connection = headers.get("connection", "").lower()
    return upgrade == "websocket" and "upgrade" in connection


async def pipe(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
    try:
        while not reader.at_eof():
            data = await reader.read(BUFFER_SIZE)
            if not data:
                break
            writer.write(data)
            await writer.drain()
    except Exception:
        pass
    finally:
        try:
            writer.close()
            await writer.wait_closed()
        except Exception:
            pass


async def proxy_to_xray(client_reader: asyncio.StreamReader, client_writer: asyncio.StreamWriter, first_bytes: bytes) -> None:
    try:
        xray_reader, xray_writer = await asyncio.open_connection(XRAY_HOST, XRAY_PORT)
        xray_writer.write(first_bytes)
        await xray_writer.drain()
        await asyncio.gather(
            pipe(client_reader, xray_writer),
            pipe(xray_reader, client_writer),
        )
    except Exception:
        try:
            client_writer.close()
            await client_writer.wait_closed()
        except Exception:
            pass


def safe_public_path(path: str) -> Path | None:
    if path == "/":
        candidate = PUBLIC_DIR / "index.html"
    else:
        candidate = PUBLIC_DIR / urllib.parse.unquote(path).lstrip("/")
        if candidate.is_dir():
            candidate = candidate / "index.html"

    try:
        resolved = candidate.resolve()
        resolved.relative_to(PUBLIC_DIR.resolve())
    except Exception:
        return None
    return resolved if resolved.exists() and resolved.is_file() else None


async def send_response(writer: asyncio.StreamWriter, status: str, body: bytes, content_type: str = "text/plain; charset=utf-8") -> None:
    headers = (
        f"HTTP/1.1 {status}\r\n"
        f"Content-Length: {len(body)}\r\n"
        f"Content-Type: {content_type}\r\n"
        "Cache-Control: no-store\r\n"
        "Connection: close\r\n"
        "Server: \r\n"
        "\r\n"
    ).encode("utf-8")
    writer.write(headers + body)
    await writer.drain()
    writer.close()
    await writer.wait_closed()


async def serve_static(writer: asyncio.StreamWriter, path: str) -> None:
    file_path = safe_public_path(path) or safe_public_path("/")
    if not file_path:
        await send_response(writer, "404 Not Found", b"Not found\n")
        return

    content = file_path.read_bytes()
    content_type = mimetypes.guess_type(str(file_path))[0] or "application/octet-stream"
    await send_response(writer, "200 OK", content, content_type)


async def handle_client(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
    try:
        header_data = await read_http_header(reader)
        method, path, _version, headers = parse_request(header_data)

        if path == WS_PATH and is_ws_upgrade(headers):
            await proxy_to_xray(reader, writer, header_data)
            return

        if method not in {"GET", "HEAD"}:
            await send_response(writer, "405 Method Not Allowed", b"Method not allowed\n")
            return

        await serve_static(writer, path)
    except Exception:
        try:
            await send_response(writer, "400 Bad Request", b"Bad request\n")
        except Exception:
            pass


async def main() -> None:
    loop = asyncio.get_running_loop()
    loop.set_exception_handler(lambda _loop, _context: None)

    start_xray()
    await wait_for_xray()

    server = await asyncio.start_server(handle_client, HOST, PORT)

    stop_event = asyncio.Event()

    def request_stop() -> None:
        stop_event.set()

    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, request_stop)
        except NotImplementedError:
            pass

    async with server:
        await stop_event.wait()

    server.close()
    await server.wait_closed()
    stop_xray()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    finally:
        stop_xray()
