"""Real TCP tests, including an intentionally interrupted transfer."""
import asyncio
import os
import socket
import threading
import time
from concurrent.futures import ThreadPoolExecutor

import httpx
import pytest
import uvicorn
from app.admin import create_admin
from app.download import create_download


@pytest.fixture
def live(env):
    ctx,a,d,root,f=env
    sockets=[];servers=[];threads=[]
    for app in (create_download(ctx),create_admin(ctx)):
        sock=socket.socket();sock.bind(('127.0.0.1',0));sock.listen();sockets.append(sock)
        server=uvicorn.Server(uvicorn.Config(app,log_level='critical',access_log=False,proxy_headers=False,timeout_graceful_shutdown=3))
        servers.append(server)
        thread=threading.Thread(target=lambda s=server,k=sock:asyncio.run(s.serve(sockets=[k])),daemon=True)
        thread.start();threads.append(thread)
    deadline=time.time()+10
    while not all(s.started for s in servers):
        assert time.time()<deadline
        time.sleep(.02)
    yield ctx,root,f,[f'http://127.0.0.1:{s.getsockname()[1]}' for s in sockets]
    for s in servers:s.should_exit=True
    for t in threads:t.join(8)
    for s in sockets:s.close()


def test_real_disconnect_resume_and_isolation(live):
    ctx,root,f,(public,admin)=live
    data=bytes(range(256))*262144 # 64 MiB: cannot all fit in loopback socket buffering.
    large=root/'real-transfer.bin';large.write_bytes(data)
    r=ctx.links.create(str(large));path='/d/'+r['url'].rsplit('/',1)[1]
    with httpx.Client(timeout=10) as client:
        for endpoint in ('/api/links','/api/login','/docs','/openapi.json','/static/app.js'):
            assert client.get(public+endpoint).status_code==404
        assert client.get(admin+'/api/links').status_code==401
        prefix=bytearray()
        with client.stream('GET',public+path) as response:
            assert response.status_code==200
            etag=response.headers['etag']
            for chunk in response.iter_bytes(65536):
                prefix.extend(chunk)
                if len(prefix)>=262144:break
        deadline=time.time()+5
        while ctx.monitor.snapshot()['active_count']:
            assert time.time()<deadline,'断线连接未及时释放'
            time.sleep(.03)
        history=ctx.db.all('SELECT * FROM requests')
        assert history[-1]['outcome']=='interrupted'
        assert len(prefix)<=history[-1]['bytes']<len(data)
        # File can be reopened for write after disconnected response closes its Windows handle.
        with open(large,'r+b'):pass
        suffix=client.get(public+path,headers={'Range':f'bytes={len(prefix)}-','If-Range':etag})
        assert suffix.status_code==206 and bytes(prefix)+suffix.content==data
        # Ensure independent admin listener stays responsive while public transfer is backpressured.
        with client.stream('GET',public+path) as response:
            assert client.get(admin+'/').status_code==200
            ctx.db.execute('UPDATE links SET enabled=0 WHERE id=?',(r['id'],))
            assert client.head(public+path).status_code==404
            assert sum(len(c) for c in response.iter_bytes())==len(data)


def test_open_windows_handle_blocks_mutation(env):
    ctx,_,_,_,f=env
    handle,st=ctx.fs.open_read(f)
    try:
        if os.name=='nt':
            with pytest.raises(PermissionError):
                with open(f,'wb'):pass
            with pytest.raises(PermissionError):f.unlink()
    finally:handle.close()
    with open(f,'r+b') as h:h.write(b'OK')


def test_concurrency_limit(live):
    ctx,root,f,(public,admin)=live
    huge=root/'busy.bin'
    with open(huge,'wb') as out:
        for _ in range(32):out.write(b'a'*1024*1024)
    r=ctx.links.create(str(huge));u=public+'/d/'+r['url'].rsplit('/',1)[1]
    ctx.monitor.limit=1
    with httpx.Client(timeout=10) as c:
        with c.stream('GET',u) as first:
            assert first.status_code==200
            assert c.get(u).status_code==503
            assert c.get(admin+'/').status_code==200
