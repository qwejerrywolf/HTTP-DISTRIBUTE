import os
import time
from pathlib import Path
import pytest
from conftest import link


def wait(a,key):
    deadline=time.time()+20
    while time.time()<deadline:
        row=next(r for r in a.get('/api/operations').json() if r['id']==key)
        if row['status'] not in ('queued','running'):return row
        time.sleep(.02)
    raise AssertionError('operation timed out')


def op(a,kind,source='',destination='',conflict='cancel'):
    r=a.post('/api/operations',json=dict(kind=kind,source=str(source),destination=str(destination),conflict=conflict))
    assert r.status_code==200,r.text
    return wait(a,r.json()['id'])


def test_drives_and_browse(env):
    _,a,_,root,f=env
    drives=a.get('/api/drives').json();assert drives and all(d['total']>=d['free'] for d in drives)
    if os.name=='nt':assert all(':' in d['path'] for d in drives)
    r=a.get('/api/browse',params={'path':str(root),'q':'中文'}).json()
    assert r['items'][0]['path']==str(f)
    assert a.get('/api/browse',params={'path':str(root/'missing')}).status_code==400


def test_copy_move_rename_mkdir(env):
    _,a,_,root,f=env
    folder=root/'新文件夹';assert op(a,'mkdir',destination=folder)['status']=='completed'
    copied=folder/f.name;assert op(a,'copy',f,copied)['status']=='completed';assert copied.read_bytes()==f.read_bytes()
    clone=root/'目录副本';assert op(a,'copy',folder,clone)['status']=='completed';assert (clone/f.name).read_bytes()==f.read_bytes()
    moved=root/'移动.bin';assert op(a,'move',copied,moved)['status']=='completed';assert not copied.exists() and moved.exists()
    renamed=root/'重命名.bin';assert op(a,'rename',moved,renamed)['status']=='completed';assert not moved.exists()
    assert op(a,'copy',f,renamed)['status']=='failed'
    assert op(a,'copy',f,renamed,'rename')['status']=='completed';assert (root/'重命名 (1).bin').exists()
    assert op(a,'copy',f,renamed,'overwrite')['status']=='completed'
    assert op(a,'size',clone)['total']==f.stat().st_size
    assert op(a,'move',clone,clone/'sub')['status']=='failed'


def test_share_consistency_guards(env):
    _,a,d,root,f=env
    r=link(a,f)
    assert op(a,'rename',f,root/'renamed')['status']=='failed'
    assert f.exists()
    assert op(a,'copy',root/'nonexistent',f,'overwrite')['status']=='failed'
    other=root/'other';other.write_bytes(b'other')
    assert op(a,'copy',other,f,'overwrite')['status']=='failed'
    a.delete('/api/links/'+str(r['id']))
    assert op(a,'rename',f,root/'renamed')['status']=='completed'
    folder=link(a,root)
    assert op(a,'rename',other,root/'new')['status']=='failed'


def test_cancel_cleanup(env,monkeypatch):
    ctx,a,_,root,f=env
    original=ctx.fs.open_read
    def slow(p):time.sleep(.2);return original(p)
    monkeypatch.setattr(ctx.fs,'open_read',slow)
    dest=root/'cancelled'
    r=a.post('/api/operations',json={'kind':'copy','source':str(f),'destination':str(dest)}).json()
    a.post('/api/operations/'+r['id']+'/cancel')
    assert wait(a,r['id'])['status']=='cancelled'
    assert f.exists() and not dest.exists() and not list(root.glob('.httpd-*'))


def test_busy_operation_does_not_hang_authorization(env):
    import threading
    ctx,a,_,_,f=env
    ready=threading.Event();release=threading.Event()
    def lock():
        with ctx.fs_lock:
            ready.set();release.wait(5)
    worker=threading.Thread(target=lock);worker.start();ready.wait(5)
    try:
        start=time.monotonic()
        assert a.post('/api/links',json={'path':str(f)}).status_code==400
        assert time.monotonic()-start<2
        assert a.get('/api/monitor').status_code==200
    finally:
        release.set();worker.join()


def test_existing_staging_file_is_never_deleted(env,monkeypatch):
    from types import SimpleNamespace
    _,a,_,root,f=env
    stage=root/'.httpd-collision';stage.write_bytes(b'preexisting content')
    monkeypatch.setattr('app.operations.uuid.uuid4',lambda:SimpleNamespace(hex='collision'))
    assert op(a,'copy',f,root/'destination.bin')['status']=='failed'
    assert stage.read_bytes()==b'preexisting content'
    assert f.exists() and not (root/'destination.bin').exists()


@pytest.mark.skipif(not os.environ.get('HTTPD_TEST_SECOND_DRIVE'),reason='需显式提供可写第二磁盘目录 HTTPD_TEST_SECOND_DRIVE')
def test_real_cross_volume(env):
    _,a,_,root,f=env
    import tempfile
    with tempfile.TemporaryDirectory(dir=os.environ['HTTPD_TEST_SECOND_DRIVE']) as other:
        dst=Path(other)/f.name
        assert dst.parent.stat().st_dev!=f.stat().st_dev,'必须是真实不同卷'
        expected=f.read_bytes()
        assert op(a,'move',f,dst)['status']=='completed'
        assert not f.exists() and dst.read_bytes()==expected
