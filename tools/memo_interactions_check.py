"""Real HTTP acceptance for persistent likes and transactional memo image editing."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import ExitStack
import io
import json
from pathlib import Path
import tempfile

import httpx
from PIL import Image

from check_support import login, server


def checks(base, root):
    with ExitStack() as stack:
        owner, other, admin = [login(base, name) for name in ('owner', 'other', 'admin')]
        for client in (owner, other, admin):
            stack.callback(client.close)
        guest = stack.enter_context(httpx.Client(base_url=base, trust_env=False))
        image = io.BytesIO()
        Image.new('RGB', (8, 8), (90, 155, 130)).save(image, format='PNG')

        def files(count, prefix='image'):
            return [('images', (f'{prefix}-{index}.png', image.getvalue(), 'image/png')) for index in range(count)]

        response = owner.post('/api/memos', data={'content': 'Original note'}, files=files(3))
        assert response.status_code == 201, response.text
        memo = response.json()
        path = f"/api/memos/{memo['id']}"
        like_path = path + '/like'
        assert memo['likes'] == [] and memo['like_count'] == 0 and not memo['liked_by_me']
        assert guest.put(like_path).status_code == guest.delete(like_path).status_code == 401
        assert owner.put(like_path, headers={'X-CSRF-Token': 'wrong'}).status_code == 403
        for _ in range(2):
            liked = owner.put(like_path)
            assert liked.status_code == 200, liked.text
            assert liked.json()['like_count'] == 1 and liked.json()['liked_by_me']
        assert other.put(like_path).json()['like_count'] == 2
        visible = guest.get(path).json()
        assert {user['username'] for user in visible['likes']} == {'owner', 'other'}
        assert visible['like_count'] == 2 and not visible['liked_by_me']
        assert owner.get('/api/memos').json()['items'][0]['liked_by_me']
        for _ in range(2):
            unliked = owner.delete(like_path).json()
            assert unliked['like_count'] == 1 and not unliked['liked_by_me']
        with ThreadPoolExecutor(max_workers=6) as workers:
            responses = list(workers.map(lambda _: owner.put(like_path), range(6)))
        assert all(response.status_code == 200 for response in responses)
        assert owner.get(path).json()['like_count'] == 2
        print('PASS authenticated/CSRF-protected likes, names, viewer state, idempotency and simultaneous requests')

        private = owner.post('/api/memos', data={'content': 'Private note', 'visibility': 'private'}).json()
        private_path = f"/api/memos/{private['id']}"
        for method in ('put', 'delete'):
            assert getattr(other, method)(private_path + '/like').status_code == 404
        assert admin.put(private_path + '/like').status_code == 200
        assert owner.get(private_path).json()['likes'][0]['username'] == 'admin'
        assert other.get(private_path).status_code == guest.get(private_path).status_code == 404
        print('PASS private memo likes obey the existing author/admin ACL')

        original = memo['attachments']
        removed = original[0]
        patch = owner.patch(path, data={'content': 'Edited with new images', 'remove_attachment_ids': json.dumps([removed['id']])}, files=files(2, 'new'))
        assert patch.status_code == 200, patch.text
        edited = patch.json()
        assert edited['content'] == 'Edited with new images' and len(edited['attachments']) == 4
        assert [a['id'] for a in edited['attachments'][:2]] == [a['id'] for a in original[1:]]
        assert owner.get(removed['url']).status_code == 404
        assert not (root / 'uploads' / removed['stored_filename']).exists()
        assert all(owner.get(a['url']).status_code == 200 for a in edited['attachments'])
        assert edited['like_count'] == 2  # Editing preserves social interactions.
        assert other.patch(path, data={'content': 'Unauthorized'}, files=files(1)).status_code == 403
        assert guest.patch(path, json={'content': 'Unauthorized'}).status_code == 401
        foreign = other.post('/api/memos', data={'content': 'Other note'}, files=files(1)).json()['attachments'][0]
        assert owner.patch(path, json={'remove_attachment_ids': [foreign['id']]}).status_code == 400
        assert owner.patch(path, json={'content': '   '}).status_code == 422
        assert owner.patch(path, json={'visibility': 'invalid'}).status_code == 422
        print('PASS text/image replacement, retained image order, file cleanup, ACL and attachment ownership')

        before_files = set((root / 'uploads').iterdir())
        invalid = files(1, 'rollback') + [('images', ('broken.png', b'invalid image', 'image/png'))]
        failed = owner.patch(path, data={'content': 'Must not be saved', 'remove_attachment_ids': json.dumps([edited['attachments'][0]['id']])}, files=invalid)
        assert failed.status_code == 400, failed.text
        after_failure = owner.get(path).json()
        assert after_failure['content'] == edited['content']
        assert after_failure['attachments'] == edited['attachments']
        assert set((root / 'uploads').iterdir()) == before_files
        assert owner.get(edited['attachments'][0]['url']).status_code == 200
        full = owner.patch(path, data={'content': 'Nine images'}, files=files(5, 'fill'))
        assert full.status_code == 200 and len(full.json()['attachments']) == 9, full.text
        before_files = set((root / 'uploads').iterdir())
        assert owner.patch(path, data={'content': 'Too many'}, files=files(1)).status_code == 400
        assert owner.get(path).json()['content'] == 'Nine images'
        assert set((root / 'uploads').iterdir()) == before_files
        assert owner.patch(path, files=[('remove_attachment_ids', (None, 'not-json'))]).status_code == 400
        assert owner.patch(path, json={'content': 'JSON remains supported'}).status_code == 200
        print('PASS invalid-upload rollback, unchanged original files/text, nine-image limit and JSON PATCH compatibility')

        assert owner.patch(path, json={'visibility': 'private'}).status_code == 200
        assert other.get(path).status_code == 404
        assert other.delete(like_path).status_code == 404
        assert owner.get(path).json()['like_count'] == 2
        assert owner.delete(path).status_code == 204
        assert owner.get(path).status_code == 404
        assert admin.delete(private_path).status_code == 204
        print('PASS visibility changes protect like identities and deleting a liked memo cascades safely')

        durable = owner.post('/api/memos', data={'content': 'Like survives restart'}).json()
        durable_path = f"/api/memos/{durable['id']}"
        owner.put(durable_path + '/like').raise_for_status()
        other.put(durable_path + '/like').raise_for_status()
        other_id = other.get('/api/auth/me').json()['id']
        assert admin.delete(f'/api/admin/users/{other_id}').status_code == 204
        assert owner.get(durable_path).json()['like_count'] == 1
        return durable_path


if __name__ == '__main__':
    with tempfile.TemporaryDirectory(prefix='apocalypse-memo-interactions-') as tmp:
        with server(root=Path(tmp)) as (base, root, _):
            durable_path = checks(base, root)
        with server(root=Path(tmp)) as (base, _, _):
            with httpx.Client(base_url=base, trust_env=False) as guest:
                memo = guest.get(durable_path).json()
                assert memo['like_count'] == 1
                assert [user['username'] for user in memo['likes']] == ['owner']
        print('PASS user deletion cleans up likes and existing-data restart preserves likes without duplicate schema changes')
