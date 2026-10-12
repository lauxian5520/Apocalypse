"""Exercise backup locks and a real restore of synthetic user data over HTTP."""
import io
from contextlib import closing
import tempfile
import tarfile
import time
from pathlib import Path
from PIL import Image
from backup import backup, restore, unpack_verified
from check_support import server, login, test_env
from mock_provider import mock_provider


def main():
    with tempfile.TemporaryDirectory(prefix='apocalypse-restore-check-') as tmp, mock_provider() as endpoint:
        root=Path(tmp)/'original';archive=Path(tmp)/'backup.tar.gz';target=Path(tmp)/'restored'
        with server(root=root,env=test_env(root,CUSTOM_API_URL=endpoint)) as (base, _, _):
            with closing(login(base,'owner')) as client:
                image=io.BytesIO();Image.new('RGB',(2,2)).save(image,format='PNG')
                for visibility in ('public','private'):
                    r=client.post('/api/memos',data={'content':visibility+' restore fixture','visibility':visibility},files={'images':('x.png',image.getvalue(),'image/png')})
                    assert r.status_code==201
                dm=client.post('/api/messages',data={'recipient_id':'2','content':'restore DM'},files={'file':('restore.txt',b'restored DM file','text/plain')})
                assert dm.status_code==201
            with closing(login(base,'admin')) as admin:
                session=admin.post('/api/harness/sessions',json={'preset':'standard','title':'restore session'}).json()
                assert session['id']
                turn=admin.post('/api/harness/sessions/'+session['id']+'/messages',json={'text':'restore synthetic history'})
                assert turn.status_code==200 and 'assistant/message' in turn.text
                history=admin.get('/api/harness/sessions/'+session['id']+'/events').json()
            try: backup(root,archive,'db/app.sqlite')
            except ValueError: pass
            else: raise AssertionError('live backup accepted')
        started=time.monotonic()
        backup(root,archive,'db/app.sqlite')
        corrupt=Path(tmp)/'corrupt.tar.gz'
        with tarfile.open(archive,'r:gz') as original, tarfile.open(corrupt,'w:gz') as changed:
            for member in original.getmembers():
                content=original.extractfile(member).read()
                if member.name=='db/app.sqlite': content=b'corrupt'+content[7:]
                changed.addfile(member,io.BytesIO(content))
        try: restore(corrupt,Path(tmp)/'corrupt-restore')
        except ValueError: pass
        else: raise AssertionError('corrupt backup accepted')
        assert not (Path(tmp)/'corrupt-restore').exists()
        restore(archive,target)
        try: restore(archive,target)
        except ValueError: pass
        else: raise AssertionError('nonempty overwrite accepted')
        with server(root=target) as (base,_,_):
            with closing(login(base,'owner')) as client:
                for visibility in ('public','private'):
                    memo=client.get('/api/memos?visibility='+visibility).json()['items'][0]
                    assert memo['content']==visibility+' restore fixture'
                    assert client.get(memo['attachments'][0]['url']).status_code==200
                assert client.get('/api/messages/with/2').json()[0]['content']=='restore DM'
                assert client.get(dm.json()['attachment_url']).content==b'restored DM file'
            with closing(login(base,'admin')) as admin:
                assert admin.get('/api/harness/sessions/'+session['id']).status_code==200
                assert admin.get('/api/harness/sessions/'+session['id']+'/events').json()==history
        second=Path(tmp)/'second.tar.gz'
        backup(target,second,'db/app.sqlite')
        restore(second,Path(tmp)/'restored-again')
        print(f'PASS stopped backup, checksum+SQLite verification, restore login/memos/files/DM/session; {time.monotonic()-started:.2f}s')


if __name__=='__main__': main()
