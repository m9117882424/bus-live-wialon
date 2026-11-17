"""Validate the analytics location without touching shared Nginx runtime paths."""
from __future__ import annotations
import os
import shutil
import subprocess
import tempfile
from pathlib import Path

LIVE_TEMP = Path("/var/lib/nginx")


def metadata():
    paths = [LIVE_TEMP, *LIVE_TEMP.glob("*")]
    return {
        str(path): (path.lstat().st_uid, path.lstat().st_gid, path.lstat().st_mode)
        for path in paths if path.exists()
    }


def configuration(runtime, locations):
    worker = "user www-data;\n" if os.geteuid() == 0 else ""
    temp_paths = "\n".join(
        f"{directive}_temp_path {runtime / directory};"
        for directive, directory in [
            ("client_body", "body"), ("proxy", "proxy"), ("fastcgi", "fastcgi"),
            ("uwsgi", "uwsgi"), ("scgi", "scgi")
        ]
    )
    return f"""{worker}pid {runtime / 'nginx.pid'};
error_log {runtime / 'error.log'};
events {{}}
http {{
access_log off;
{temp_paths}
limit_req_zone $binary_remote_addr zone=bus_domain_lookup:10m rate=2r/s;
server {{
listen 127.0.0.1:18449;
server_name bus.tsmenerji.online;
{locations}
}}
}}
"""


def main():
    nginx = shutil.which("nginx")
    if not nginx:
        raise SystemExit("nginx is not installed")
    locations = Path(__file__).with_name("nginx-public-analytics.locations.conf").read_text()
    before = metadata()
    with tempfile.TemporaryDirectory(prefix="bus-analytics-nginx-") as directory:
        runtime = Path(directory)
        config = runtime / "nginx.conf"
        config.write_text(configuration(runtime, locations))
        result = subprocess.run(
            [nginx, "-t", "-p", str(runtime) + "/", "-c", str(config)],
            capture_output=True, text=True
        )
        after = metadata()
        changed = [path for path, value in before.items() if after.get(path) != value]
        if changed:
            raise SystemExit("Shared Nginx directory metadata changed: " + ", ".join(changed))
        if result.returncode:
            raise SystemExit(result.stderr)
    print("PASS: analytics Nginx configuration; shared directory owners and modes unchanged")


if __name__ == "__main__":
    main()
