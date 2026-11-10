import socket
import time
from pathlib import Path

from conftest import link
from test_operations import wait
from test_live import live


def upload(a, root, name, data, conflict='cancel', size=None):
    return a.put('/api/files/upload', params=dict(directory=str(root), name=name, size=len(data) if size is None else size, conflict=conflict),
                 content=data, headers={'content-type': 'application/octet-stream'})


def test_upload_unicode_empty_and_conflicts(env):
    ctx,a,d,root,_=env
    assert upload(a,root,'中文 上传.txt',b'hello').status_code==200
    assert (root/'中文 上传.txt').read_bytes()==b'hello'
    assert upload(a,root,'empty.txt',b'').status_code==200
    assert upload(a,root,'中文 上传.txt',b'new').status_code==400
    assert upload(a,root,'中文 上传.txt',b'renamed','rename').status_code==200
    assert (root/'中文 上传 (1).txt').read_bytes()==b'renamed'
    assert upload(a,root,'中文 上传.txt',b'replaced','overwrite').status_code==200
    assert (root/'中文 上传.txt').read_bytes()==b'replaced'
    link(a,root/'中文 上传.txt')
    assert upload(a,root,'中文 上传.txt',b'blocked','overwrite').status_code==400
    assert (root/'中文 上传.txt').read_bytes()==b'replaced'
    assert not list(root.glob('.httpd-*'))
    assert not ctx.file_changes.uploads


def test_upload_validation_security_and_cleanup(env):
    ctx,a,d,root,_=env
    for name in ('../outside','x/y','x\\y','file:ads','.httpd-test','CON','trailing.'):
        assert upload(a,root,name,b'no').status_code==400
    assert upload(a,ctx.db.directory,'data.txt',b'no').status_code==400
    assert upload(a,root,'short',b'abc',size=5).status_code==400
    assert upload(a,root,'long',b'abc',size=2).status_code==400
    assert not (root/'short').exists() and not (root/'long').exists()
    assert not list(root.glob('.httpd-*'))
    assert a.put('/api/files/upload',params=dict(directory=str(root),name='no',size=0),content=b'',headers={'x-csrf-token':'bad'}).status_code==403
    assert d.put('/api/files/upload').status_code==404


def test_confirmed_batch_delete_files_and_folders(env):
    ctx,a,d,root,f=env
    folder=root/'删除目录';folder.mkdir();sub=folder/'nested';sub.mkdir();(sub/'inside.txt').write_text('inside')
    paths=[str(f),str(folder),str(sub)] # Parent selection subsumes children.
    preview=a.post('/api/files/delete-preview',json={'paths':paths})
    assert preview.status_code==200,preview.text
    p=preview.json()
    assert {r['path'] for r in p['items']}=={str(f),str(folder)}
    assert p['files']==2 and p['folders']==2
    assert f.exists() and folder.exists() # Preview cannot delete.
    result=a.post('/api/files/delete',json={'confirmation':p['confirmation']})
    assert result.status_code==200
    row=wait(a,result.json()['id'])
    assert row['status']=='completed' and row['done']==4
    assert not f.exists() and not folder.exists()
    assert a.post('/api/files/delete',json={'confirmation':p['confirmation']}).status_code==400
    assert d.post('/api/files/delete').status_code==404


def test_delete_revalidates_snapshot_and_links(env):
    _,a,_,root,f=env
    preview=a.post('/api/files/delete-preview',json={'paths':[str(f)]}).json()
    f.write_bytes(b'changed after preview')
    row=wait(a,a.post('/api/files/delete',json={'confirmation':preview['confirmation']}).json()['id'])
    assert row['status']=='failed' and row['done']==0 and f.exists()
    preview=a.post('/api/files/delete-preview',json={'paths':[str(f)]}).json()
    link(a,f)
    row=wait(a,a.post('/api/files/delete',json={'confirmation':preview['confirmation']}).json()['id'])
    assert row['status']=='failed' and f.exists()
    assert a.post('/api/files/delete-preview',json={'paths':[str(root)]}).status_code==400


def test_delete_protected_paths_csrf_and_owner(env):
    ctx,a,_,root,f=env
    for path in (ctx.db.directory,Path(root.anchor)):
        assert a.post('/api/files/delete-preview',json={'paths':[str(path)]}).status_code==400
    assert a.post('/api/files/delete-preview',json={'paths':[str(f)]},headers={'x-csrf-token':'bad'}).status_code==403
    preview=ctx.file_changes.preview_delete([str(f)],'first-session')
    try:
        ctx.file_changes.delete(preview['confirmation'],'second-session')
        assert False,'another session must not consume confirmation'
    except ValueError:pass
    assert f.exists()


def test_upload_reservation_and_commit_race(env):
    ctx,a,_,root,_=env
    pending=ctx.file_changes.begin_upload(str(root),'race.txt',3,'cancel')
    try:
        ctx.file_changes.write_upload(pending,b'new')
        assert a.post('/api/files/delete-preview',json={'paths':[str(root)]}).status_code==400
        (root/'race.txt').write_bytes(b'external')
        try:
            ctx.file_changes.commit_upload(pending)
            assert False,'must not overwrite a newly created destination'
        except ValueError:pass
        ctx.file_changes.abort_upload(pending,'expected collision')
        assert (root/'race.txt').read_bytes()==b'external'
    finally:ctx.file_changes.release_upload(pending)


def test_cancel_queued_delete_keeps_all_sources(env):
    ctx,a,_,root,f=env
    preview=ctx.file_changes.preview_delete([str(f)],'owner')
    with ctx.fs_lock:
        queued=ctx.file_changes.delete(preview['confirmation'],'owner')
        ctx.operations.cancel(queued['id'])
    row=wait(a,queued['id'])
    assert row['status']=='cancelled' and row['done']==0 and f.exists()


def test_delete_and_upload_reject_junction(env,tmp_path):
    import os
    import subprocess
    import pytest
    _,a,_,root,_=env
    outside=tmp_path/'outside';outside.mkdir();secret=outside/'keep.txt';secret.write_text('preserve')
    junction=root/'junction'
    if os.name=='nt':
        result=subprocess.run(['cmd','/c','mklink','/J',str(junction),str(outside)],capture_output=True)
        if result.returncode:pytest.skip('无法创建测试 Junction')
    else:junction.symlink_to(outside,target_is_directory=True)
    try:
        assert a.post('/api/files/delete-preview',json={'paths':[str(root)]}).status_code==400
        assert upload(a,junction,'keep.txt',b'bad','overwrite').status_code==400
        assert secret.read_text()=='preserve'
    finally:
        junction.rmdir() if os.name=='nt' else junction.unlink()


def test_real_upload_disconnect_cleans_staging(live):
    import httpx
    from urllib.parse import urlencode, urlsplit
    ctx,root,_,(_,admin)=live
    with httpx.Client(base_url=admin,headers={'x-httpd-request':'1'}) as client:
        response=client.post('/api/login',json={'username':'admin','password':'test-password-only-123'})
        csrf=response.json()['csrf'];cookie=client.cookies.get('httpd_session')
    address=urlsplit(admin)
    sock=socket.create_connection((address.hostname,address.port))
    target='/api/files/upload?'+urlencode(dict(directory=str(root),name='disconnect.bin',size=10000000))
    headers=f'PUT {target} HTTP/1.1\r\nHost: {address.netloc}\r\nContent-Length: 10000000\r\nContent-Type: application/octet-stream\r\nX-HTTPD-Request: 1\r\nX-CSRF-Token: {csrf}\r\nCookie: httpd_session={cookie}\r\n\r\n'
    try:
        sock.sendall(headers.encode()+b'a'*10000)
        deadline=time.time()+5
        while not list(root.glob('.httpd-upload-*')):
            assert time.time()<deadline
            time.sleep(.02)
    finally:sock.close()
    deadline=time.time()+5
    while ctx.file_changes.uploads:
        assert time.time()<deadline,'上传断线后未释放'
        time.sleep(.02)
    assert not list(root.glob('.httpd-upload-*')) and not (root/'disconnect.bin').exists()
