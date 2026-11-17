"""Test-only server launcher. Default: loopback Uvicorn + temporary SQLite.

BUS_TEST_TRANSPORT=asgi explicitly selects a socket-free HTTP transport for
restricted sandboxes. Requests still traverse the real FastAPI ASGI application,
including routing, validation, middleware, auth, cookies and SQLite transactions.
No production environment files or databases are read.
"""
import asyncio
import importlib.util
import os
from pathlib import Path
import socket
import subprocess
import sys
import threading
from types import SimpleNamespace
from email.message import Message
from urllib.parse import urlsplit

import requests


def unused_port():
    if os.environ.get('BUS_TEST_TRANSPORT') == 'asgi':
        return 18399
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        return sock.getsockname()[1]


def start_server(command, *, cwd, env, **kwargs):
    if os.environ.get('BUS_TEST_TRANSPORT') != 'asgi':
        return subprocess.Popen(command, cwd=cwd, env=env, **kwargs)
    return ASGIServer(cwd, env)


class ASGIServer:
    def __init__(self, root, env):
        from uvicorn.middleware.proxy_headers import ProxyHeadersMiddleware
        self.old_env = {k: v for k, v in os.environ.items() if k.startswith('BUS_')}
        for k in list(os.environ):
            if k.startswith('BUS_'):
                del os.environ[k]
        os.environ.update({k: v for k, v in env.items() if k.startswith('BUS_')})
        sys.path.insert(0, str(root))
        spec = importlib.util.spec_from_file_location('isolated_bus_app', Path(root) / 'app.py')
        self.module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.module)
        sys.path.pop(0)
        self.module.init()
        self.app = ProxyHeadersMiddleware(self.module.app, trusted_hosts=['127.0.0.1'])
        self.loop = asyncio.new_event_loop()
        self.thread = threading.Thread(target=self.loop.run_forever, daemon=True)
        self.thread.start()
        self.old_send = requests.adapters.HTTPAdapter.send
        server = self

        def send(_adapter, request, **_kwargs):
            parsed = urlsplit(request.url)
            if parsed.hostname != '127.0.0.1':
                raise AssertionError('Isolated test attempted a non-loopback request')
            return asyncio.run_coroutine_threadsafe(server.respond(request), server.loop).result(timeout=30)
        requests.adapters.HTTPAdapter.send = send

    async def respond(self, request):
        parsed = urlsplit(request.url)
        headers = [(k.lower().encode('latin1'), v.encode('latin1')) for k, v in request.headers.items()]
        if not any(k == b'host' for k, _ in headers):
            headers.append((b'host', parsed.netloc.encode()))
        scope = {'type': 'http', 'asgi': {'version': '3.0', 'spec_version': '2.4'},
                 'http_version': '1.1', 'method': request.method, 'scheme': parsed.scheme,
                 'path': parsed.path, 'raw_path': parsed.path.encode(), 'query_string': parsed.query.encode(),
                 'headers': headers, 'client': ('127.0.0.1', 32100),
                 'server': ('127.0.0.1', parsed.port or 80), 'root_path': ''}
        body = request.body or b''
        if isinstance(body, str):
            body = body.encode()
        sent = False
        messages = []

        async def receive():
            nonlocal sent
            if not sent:
                sent = True
                return {'type': 'http.request', 'body': body, 'more_body': False}
            await asyncio.Future()

        async def send(message):
            messages.append(message)

        await self.app(scope, receive, send)
        head = next(m for m in messages if m['type'] == 'http.response.start')
        response = requests.Response()
        response.status_code = head['status']
        response.url = request.url
        response.request = request
        response._content = b''.join(m.get('body', b'') for m in messages if m['type'] == 'http.response.body')
        response.encoding = 'utf-8'
        msg = Message()
        for key, value in head['headers']:
            key, value = key.decode('latin1'), value.decode('latin1')
            response.headers[key] = value
            msg.add_header(key, value)
        response.raw = SimpleNamespace(_original_response=SimpleNamespace(msg=msg))
        requests.cookies.extract_cookies_to_jar(response.cookies, request, response.raw)
        return response

    def terminate(self):
        requests.adapters.HTTPAdapter.send = self.old_send
        self.loop.call_soon_threadsafe(self.loop.stop)
        self.thread.join(timeout=5)
        self.loop.close()
        for k in list(os.environ):
            if k.startswith('BUS_'):
                del os.environ[k]
        os.environ.update(self.old_env)

    kill = terminate

    def wait(self, timeout=None):
        return 0
