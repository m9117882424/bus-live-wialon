import os
import sys
import socket
import subprocess
import tempfile
import time
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from isolated_server import start_server, unused_port

import requests
from cryptography.fernet import Fernet

ROOT = Path(__file__).resolve().parents[2]
PYTHON = sys.executable


with tempfile.TemporaryDirectory(prefix="bus-chrome-pwa-") as temporary:
    port = unused_port()
    origin = f"http://127.0.0.1:{port}"
    env = dict(os.environ, BUS_DB=str(Path(temporary) / "app.sqlite"),
               BUS_ADMIN_PASSWORD="test-only-chrome-pwa",
               BUS_FERNET_KEY=Fernet.generate_key().decode(), BUS_PUBLIC_URL=origin)
    process = start_server(
        [PYTHON, "-m", "uvicorn", "app:app", "--host", "127.0.0.1", "--port",
         str(port), "--no-access-log"], env=env, cwd=ROOT,
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        for _ in range(100):
            try:
                if requests.get(origin + "/bus/scanner/", timeout=1).status_code == 200:
                    break
            except requests.RequestException:
                pass
            time.sleep(.1)
        else:
            raise AssertionError("Test app did not start")

        page = requests.get(origin + "/bus/scanner/", timeout=5)
        assert page.status_code == 200
        assert 'rel="manifest" href="/bus/scanner/manifest.webmanifest"' in page.text
        assert 'id="installPanel"' in page.text and 'id="installBtn"' in page.text
        assert 'beforeinstallprompt' in page.text and 'appinstalled' in page.text
        assert 'id="pinBtn"' in page.text and 'id="manualBtn"' in page.text
        manifest_response = requests.get(origin + "/bus/scanner/manifest.webmanifest", timeout=5)
        assert manifest_response.status_code == 200
        assert "application/manifest+json" in manifest_response.headers["content-type"]
        manifest = manifest_response.json()
        assert manifest["id"] == "/bus/scanner/"
        assert manifest["start_url"] == manifest["scope"] == "/bus/scanner/"
        assert manifest["display"] == "standalone"
        assert manifest["name"] and manifest["short_name"] and manifest["description"]
        assert len(manifest["icons"]) == 2
        for n in (192, 512):
            icon = next(i for i in manifest["icons"] if i["sizes"] == f"{n}x{n}")
            assert icon["src"] == f"/bus/scanner/icon-{n}.png"
            image = requests.get(origin + icon["src"], timeout=5)
            assert image.status_code == 200
            assert image.headers["content-type"].startswith("image/png")
            assert image.content[:8] == bytes.fromhex('89504e470d0a1a0a'), image.content[:8]
            assert int.from_bytes(image.content[16:20], "big") == n
            assert int.from_bytes(image.content[20:24], "big") == n
        worker = requests.get(origin + "/bus/scanner/sw.js", timeout=5)
        assert worker.status_code == 200
        assert "text/javascript" in worker.headers["content-type"]
        assert "addEventListener('fetch'" in worker.text
        assert "fetch(e.request)" in worker.text
        assert "caches.open" not in worker.text, "Boarding results must never be cached"
        assert requests.get(origin + "/bus/api/admin/settings", timeout=5).status_code == 401
        print("PASS: Chrome manifest, persistent ID/scope/standalone, icons 192/512, service worker, live scanner UI and admin auth")
    finally:
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)
