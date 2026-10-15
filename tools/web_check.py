"""Real HTTP regression checks on temporary state; no external providers."""
from contextlib import closing
import hashlib
import hmac
import io
import json
import sqlite3
import subprocess
import sys
import tempfile
import time
from pathlib import Path
import httpx
from PIL import Image
from check_support import server, login, test_env, PASSWORD, ROOT


def check_registration_defaults() -> None:
    """Keep first-admin access while preventing signup from self-granting permissions."""
    with server(seed_accounts=False) as (base, _, env), httpx.Client(base_url=base, trust_env=False) as client:
        timestamp = int(time.time())
        signature = hmac.new(env['JWT_SECRET'].encode(), f'ABCD:{timestamp}'.encode(), hashlib.sha256).hexdigest()
        for name, expected_role, expected_access in (('first', 'admin', True), ('friend', 'user', False)):
            response = client.post('/api/auth/register', json={
                'username': name, 'email': name+'@example.invalid', 'password': PASSWORD,
                'captcha': 'ABCD', 'captcha_token': f'{timestamp}:{signature}',
                'role': 'admin', 'can_use_harness': True,
            })
            assert response.status_code == 201, response.text
            assert response.json()['role'] == expected_role
            assert response.json()['can_use_harness'] is expected_access
        response = client.post('/api/auth/login', json={'username': 'friend', 'password': PASSWORD})
        assert response.status_code == 200
        assert response.json()['user']['can_use_harness'] is False
        assert client.get('/api/harness/sessions').status_code == 403
        assert client.get('/api/admin/users').status_code == 403
        response = client.post('/api/auth/login', json={'username': 'first', 'password': PASSWORD})
        assert response.status_code == 200
        assert response.json()['user']['can_use_harness'] is True
        assert client.get('/api/harness/sessions').status_code == 200
    print('PASS first-admin and regular-signup defaults, including forged permissions in signup')


def check_user_migration() -> None:
    """Upgrade a legacy users table and preserve subsequent revocations."""
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        (root / 'db').mkdir()
        with closing(sqlite3.connect(root / 'db/app.sqlite')) as db, db:
            db.execute('''CREATE TABLE users (
                id INTEGER PRIMARY KEY, username VARCHAR(50) UNIQUE NOT NULL,
                email VARCHAR(255) UNIQUE NOT NULL, password_hash VARCHAR(255) NOT NULL,
                avatar_url VARCHAR(500), role VARCHAR(5) NOT NULL,
                is_disabled BOOLEAN DEFAULT FALSE NOT NULL,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                updated_at DATETIME DEFAULT CURRENT_TIMESTAMP)''')
            for name, role in (('legacy_admin', 'admin'), ('legacy_user', 'user')):
                db.execute('INSERT INTO users (username,email,password_hash,role) VALUES (?,?,?,?)',
                           (name, name+'@example.invalid', 'unused-fixture', role))
        command = [sys.executable, '-c', 'from models.migrations import init_database; init_database()']
        subprocess.run(command, cwd=ROOT/'backend', env=test_env(root), check=True)
        with closing(sqlite3.connect(root / 'db/app.sqlite')) as db, db:
            assert db.execute('SELECT can_use_harness FROM users ORDER BY id').fetchall() == [(1,), (0,)]
            db.execute("UPDATE users SET can_use_harness=FALSE WHERE role='admin'")
            db.execute("INSERT INTO users (username,email,password_hash,role) VALUES ('new_user','new@example.invalid','unused','user')")
        subprocess.run(command, cwd=ROOT/'backend', env=test_env(root), check=True)
        with closing(sqlite3.connect(root / 'db/app.sqlite')) as db, db:
            assert db.execute('SELECT can_use_harness FROM users ORDER BY id').fetchall() == [(0,), (0,), (0,)]
    print('PASS legacy user migration, default-deny and durable Harness revocation')


def check_user_management(clients: dict[str, httpx.Client]) -> None:
    """Exercise real cookie-authenticated authorization and self-service renaming."""
    owner, other, admin = (clients[name] for name in ('owner', 'other', 'admin'))
    owner_info, other_info, admin_info = (client.get('/api/auth/me').json() for client in (owner, other, admin))
    owner_id, other_id, admin_id = (info['id'] for info in (owner_info, other_info, admin_info))
    permission_url = f'/api/admin/users/{owner_id}/harness-permission'
    assert owner_info['can_use_harness'] is False
    assert admin_info['can_use_harness'] is True
    assert clients['anonymous'].patch(permission_url, json={'can_use_harness': True}).status_code == 401
    assert admin.patch('/api/admin/users/99999/harness-permission', json={'can_use_harness': True}).status_code == 404

    # Being an administrator alone must not grant access; granting access must not grant admin.
    assert admin.patch(f'/api/admin/users/{owner_id}/role', json={'role': 'admin'}).status_code == 200
    assert owner.get('/api/harness/sessions').status_code == 403
    assert admin.patch(f'/api/admin/users/{owner_id}/role', json={'role': 'user'}).status_code == 200
    assert admin.patch(permission_url, json={'can_use_harness': True}).status_code == 200
    assert owner.get('/api/auth/me').json()['role'] == 'user'
    assert owner.get('/api/auth/me').json()['can_use_harness'] is True
    assert owner.get('/api/harness/sessions').status_code == 200
    assert owner.get('/api/admin/users').status_code == 403
    for suffix, payload in (('harness-permission', {'can_use_harness': True}),
                            ('role', {'role': 'user'}), ('status', {'is_disabled': True}),
                            ('reset-password', {'new_password': PASSWORD})):
        assert owner.patch(f'/api/admin/users/{admin_id}/{suffix}', json=payload).status_code == 403
    assert owner.delete(f'/api/admin/users/{admin_id}').status_code == 403

    session = owner.post('/api/harness/sessions', json={})
    assert session.status_code == 201, session.text
    session_url = '/api/harness/sessions/' + session.json()['id']
    assert admin.patch(f'/api/admin/users/{other_id}/harness-permission', json={'can_use_harness': True}).status_code == 200
    assert other.get(session_url).status_code == 403
    assert admin.patch(f'/api/admin/users/{other_id}/harness-permission', json={'can_use_harness': False}).status_code == 200
    assert admin.patch(f'/api/admin/users/{owner_id}/status', json={'is_disabled': True}).status_code == 200
    assert owner.get('/api/harness/sessions').status_code == 403
    assert admin.patch(f'/api/admin/users/{owner_id}/status', json={'is_disabled': False}).status_code == 200
    assert owner.get(session_url).status_code == 200
    assert admin.patch(permission_url, json={'can_use_harness': False}).status_code == 200
    for path in ('/api/harness/sessions', session_url, session_url+'/events', session_url+'/files',
                 session_url+'/archive', '/api/harness/tools', '/api/harness/skills', '/api/harness/agents', '/api/harness/presets'):
        assert owner.get(path).status_code == 403, path
    assert owner.post('/api/harness/sessions', json={}).status_code == 403
    assert owner.post(session_url+'/messages', json={'text': 'denied'}).status_code == 403
    assert admin.patch(permission_url, json={'can_use_harness': True}).status_code == 200
    assert owner.get(session_url).status_code == 200
    own_permission = f'/api/admin/users/{admin_id}/harness-permission'
    assert admin.patch(own_permission, json={'can_use_harness': False}).status_code == 200
    assert admin.get('/api/harness/sessions').status_code == 403
    assert admin.get('/api/admin/users').status_code == 200
    assert admin.patch(own_permission, json={'can_use_harness': True}).status_code == 200
    print('PASS independent Harness permission, admin isolation, session ownership, disable and immediate revocation')

    assert clients['anonymous'].patch('/api/auth/me', json={'username': 'anonymous'}).status_code == 401
    csrf = owner.headers.pop('X-CSRF-Token')
    assert owner.patch('/api/auth/me', json={'username': 'csrf'}).status_code == 403
    owner.headers['X-CSRF-Token'] = csrf
    for payload in ({'username': ''}, {'username': ' \t '}, {'username': 'x'*51}, {'username': None},
                    {'username': 'attempt', 'role': 'admin'}, {'username': 'attempt', 'can_use_harness': True},
                    {'username': 'attempt', 'id': other_id}):
        assert owner.patch('/api/auth/me', json=payload).status_code == 422, payload
    assert owner.patch('/api/auth/me', json={'username': ' other '}).status_code == 400
    assert owner.patch('/api/auth/me', json={'username': 'x'*50}).status_code == 200
    before_rename = owner.get('/api/auth/me').json()
    renamed = '改名后的用户'
    response = owner.patch('/api/auth/me', json={'username': '  '+renamed+'  '})
    assert response.status_code == 200, response.text
    assert response.json()['username'] == renamed
    for field in ('id', 'email', 'role', 'can_use_harness', 'created_at'):
        assert response.json()[field] == before_rename[field]
    assert response.json()['id'] == owner_id
    assert owner.get('/api/auth/me').json()['username'] == renamed
    assert owner.get(f'/api/users/{owner_id}/profile').json()['username'] == renamed
    assert other.get('/api/auth/me').json()['username'] == 'other'
    assert owner.get(session_url).status_code == 200
    assert owner.patch('/api/auth/me', json={'username': renamed}).status_code == 200
    with httpx.Client(base_url=str(owner.base_url), trust_env=False) as fresh:
        assert fresh.post('/api/auth/login', json={'username': 'owner', 'password': PASSWORD}).status_code == 401
        result = fresh.post('/api/auth/login', json={'username': renamed, 'password': PASSWORD})
        assert result.status_code == 200, result.text
        assert result.json()['user']['id'] == owner_id
    assert owner.patch('/api/auth/me', json={'username': 'owner'}).status_code == 200
    print('PASS username normalization/validation, uniqueness, self-only updates, CSRF, retained session and new login')


def check():
    check_user_migration()
    check_registration_defaults()
    with tempfile.TemporaryDirectory() as tmp, server(root=tmp,env=test_env(tmp,REGISTRATION_ENABLED='false',HARNESS_REQUIRE_ADMIN='false')) as (base, root, _):
        clients = {name: login(base, name) for name in ('owner', 'other', 'admin')}
        clients['anonymous'] = httpx.Client(base_url=base, trust_env=False)
        owner = clients['owner']
        assert clients['anonymous'].post('/api/auth/register',json={}).status_code==403
        image = io.BytesIO()
        Image.new('RGB', (2, 2)).save(image, format='PNG')
        data = image.getvalue()
        def create(visibility):
            response = owner.post('/api/memos', data={'content': 'private-fixture', 'visibility': visibility}, files={'images': ('image.html', data, 'image/png')})
            assert response.status_code == 201, response.text
            return response.json()
        public, private = create('public'), create('private')
        for memo in (public, private):
            for name, client in clients.items():
                expected = 200 if memo['visibility']=='public' or name in ('owner','admin') else 404
                for url in (f"/api/memos/{memo['id']}", memo['attachments'][0]['url']):
                    response = client.get(url)
                    assert response.status_code == expected, (name, url, response.status_code)
        print('PASS memo and attachment ACL matrix (anonymous/owner/other/admin)')
        (root/'uploads/legacy.html').write_text('<script>alert(1)</script>')
        with sqlite3.connect(root/'db/app.sqlite') as db:
            db.execute('INSERT INTO attachments (memo_id, original_name, stored_filename, mime_type, size_bytes) VALUES (?, ?, ?, ?, ?)',
                       (private['id'],'old.png','legacy.html','image/png',25))
        for name,client in clients.items():
            response=client.get('/api/uploads/legacy.html')
            assert response.status_code==(200 if name in ('owner','admin') else 404)
            if response.status_code==200: assert 'attachment' in response.headers['content-disposition']
        for name, client in clients.items():
            response = client.get('/api/memos?visibility=private')
            if name == 'anonymous':
                assert response.status_code == 403
            else:
                assert response.status_code == 200
                assert response.json()['total'] == (0 if name == 'other' else 1)
        assert owner.get('/api/memos?visibility=typo').status_code == 422
        assert owner.patch(f"/api/memos/{private['id']}", json={'visibility':'typo'}).status_code == 422
        assert clients['other'].post(f"/api/memos/{private['id']}/comments", json={'content':'attack'}).status_code == 404
        comment = owner.post(f"/api/memos/{public['id']}/comments", data={'content':'image'}, files={'image':('c.png',data,'image/png')})
        assert comment.status_code == 201, comment.text
        owner.patch(f"/api/memos/{public['id']}", json={'visibility':'private'})
        assert clients['anonymous'].get(public['attachments'][0]['url']).status_code == 404
        assert clients['other'].get(comment.json()['image_url']).status_code == 404
        assert clients['admin'].get(comment.json()['image_url']).status_code == 200
        assert clients['other'].get('/api/users/1/profile').json()['memo_count'] == 0
        print('PASS private lists, comments, profile counts and public-to-private transition')
        msg = owner.post('/api/messages', data={'recipient_id':'2','content':'file'}, files={'file':('active.html',b'<script>alert(1)</script>','text/html')})
        assert msg.status_code == 201, msg.text
        for name, client in clients.items():
            response = client.get(msg.json()['attachment_url'])
            assert response.status_code == (200 if name in ('owner','other') else 404)
            if response.status_code == 200:
                assert 'attachment' in response.headers['content-disposition']
                assert 'no-store' in response.headers['cache-control']
                assert response.headers['x-content-type-options']=='nosniff'
        print('PASS DM files restricted to participants, active content downloads')
        before = set((root/'uploads').glob('*'))
        bad = owner.post('/api/memos', data={'content':'bad'}, files=[('images',('a.png',data,'image/png')),('images',('b.png',b'not an image','image/png'))])
        assert bad.status_code == 400
        assert set((root/'uploads').glob('*')) == before
        bad=owner.post('/api/memos',data={'content':'oversize'},files={'images':('large.png',b'x'*(10*1024*1024+1),'image/png')})
        assert bad.status_code==400
        assert set((root/'uploads').glob('*'))==before
        bad = owner.post('/api/messages', data={'recipient_id':'99999'}, files={'file':('x.txt',b'bad','text/plain')})
        assert bad.status_code == 404
        assert set((root/'uploads').glob('*')) == before
        (root/'uploads/orphan.txt').write_text('orphan')
        assert owner.get('/api/uploads/orphan.txt').status_code == 404
        assert owner.get('/api/uploads/..%2Fdb%2Fapp.sqlite').status_code == 404
        print('PASS upload validation, rollback cleanup, orphan and traversal denial')
        csrf = owner.headers.pop('X-CSRF-Token')
        assert owner.post('/api/memos', data={'content':'csrf'}).status_code == 403
        owner.headers['X-CSRF-Token'] = csrf
        assert clients['other'].get('/api/harness/sessions').status_code == 403
        assert clients['anonymous'].get('/api/auth/me').status_code == 401
        assert owner.get('/healthz').status_code == owner.get('/readyz').status_code == 200
        assert owner.get('/memos.html').status_code == 200
        print('PASS cookie CSRF, Harness permission gate, static page and readiness')
        check_user_management(clients)
        for client in clients.values():
            client.close()


if __name__ == '__main__':
    check()
