"""Real HTTP regression checks on temporary state; no external providers."""
import io
import json
import sqlite3
import tempfile
from pathlib import Path
import httpx
from PIL import Image
from check_support import server, login, test_env


def check():
    with tempfile.TemporaryDirectory() as tmp, server(root=tmp,env=test_env(tmp,REGISTRATION_ENABLED='false')) as (base, root, _):
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
        print('PASS cookie CSRF, Harness admin gate, static page and readiness')
        for client in clients.values():
            client.close()


if __name__ == '__main__':
    check()
