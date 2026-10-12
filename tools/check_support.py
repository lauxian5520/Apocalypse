"""Isolated HTTP test server shared by business, browser and live acceptance."""
import contextlib
import os
from pathlib import Path
import socket
import shutil
import subprocess
import sys
import tempfile
import time
import httpx

ROOT = Path(__file__).resolve().parents[1]
PASSWORD = 'Review-fixture-42!'


def free_port():
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        return sock.getsockname()[1]


def test_env(root, **extra):
    root = Path(root)
    env = dict(os.environ, APP_ENV='test', VAR_DIR=str(root), DB_TYPE='sqlite',
               SQLITE_PATH=str(root/'db/app.sqlite'), UPLOAD_DIR=str(root/'uploads'),
               MUSIC_DIR=str(root/'music'), FEEDS_DIR=str(root/'feeds'), HARNESS_DIR=str(root/'harness'),
               JWT_SECRET='isolated-test-secret-not-for-deployment', COOKIE_SECURE='false',
               REGISTRATION_ENABLED='true', FEEDS_ENABLED='false', HARNESS_AUTO_TITLE='false',
               HARNESS_SHELL_ENABLED='false', HARNESS_REQUIRE_ADMIN='true',
               AI_PROVIDER='custom', CUSTOM_API_URL='http://127.0.0.1:1/v1/chat/completions',
               CUSTOM_API_KEY='test-only', CUSTOM_MODEL='mock', HARNESS_MODEL='',
               PYTHONDONTWRITEBYTECODE='1')
    env.update(extra)
    return env


def seed(env):
    code = '''
from models.migrations import init_database
from core.database import SessionLocal
from core.security import hash_password
from models.user import User
init_database()
with SessionLocal() as db:
    for name in ('owner', 'other', 'admin'):
        if not db.query(User).filter(User.username == name).first():
            db.add(User(username=name, email=name+'@example.invalid', password_hash=hash_password('Review-fixture-42!'), role='admin' if name=='admin' else 'user'))
    db.commit()
'''
    subprocess.run([sys.executable, '-c', code], cwd=ROOT/'backend', env=env, check=True)


@contextlib.contextmanager
def server(root=None, env=None):
    with tempfile.TemporaryDirectory(prefix='apocalypse-http-') as tmp:
        root = Path(root or tmp)
        root.mkdir(parents=True, exist_ok=True)
        env = env or test_env(root)
        seed(env)
        port = free_port()
        log_path = root/'server.log'
        with log_path.open('w') as log:
            process = subprocess.Popen([sys.executable, '-m', 'uvicorn', 'main:app', '--host', '127.0.0.1', '--port', str(port), '--no-access-log'], cwd=ROOT/'backend', env=env, stdout=log, stderr=log)
            base = f'http://127.0.0.1:{port}'
            try:
                for _ in range(200):
                    if process.poll() is not None:
                        raise RuntimeError('test server exited; inspect '+str(log_path))
                    try:
                        if httpx.get(base+'/readyz', timeout=1, trust_env=False).status_code == 200:
                            break
                    except httpx.HTTPError:
                        pass
                    time.sleep(.1)
                else:
                    raise RuntimeError('test server readiness timeout')
                yield base, root, env
            finally:
                if sys.exc_info()[0] is not None:
                    output=ROOT/"artifacts/server"
                    output.mkdir(parents=True,exist_ok=True)
                    log.flush()
                    shutil.copyfile(log_path,output/f"{port}.log")
                process.terminate()
                try:
                    process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait()


def login(base, username):
    client = httpx.Client(base_url=base, trust_env=False, timeout=20)
    response = client.post('/api/auth/login', json={'username': username, 'password': PASSWORD})
    assert response.status_code == 200, response.text
    client.headers['X-CSRF-Token'] = client.cookies.get('mw_csrf')
    return client
