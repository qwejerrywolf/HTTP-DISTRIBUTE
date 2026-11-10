import os
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from urllib.parse import urlsplit

import pytest
from conftest import link


def url(r):
    return urlsplit(r['url']).path


def test_get_head_headers(env):
    ctx, a, d, root, f = env
    u = url(link(a, f))
    r = d.get(u)
    assert r.status_code == 200 and r.content == f.read_bytes()
    assert r.headers['accept-ranges'] == 'bytes'
    assert r.headers['etag'].startswith('"') and r.headers['last-modified']
    assert "filename*=utf-8''" in r.headers['content-disposition']
    h = d.head(u, headers={'Range':'bytes=2-7'})
    assert h.status_code == 200 and not h.content
    assert int(h.headers['content-length']) == f.stat().st_size
    assert h.headers['etag'] == r.headers['etag']


@pytest.mark.parametrize('header,start,end', [('bytes=0-0',0,1),('bytes=0-99',0,100),('bytes=500-699',500,700),('bytes=1048575-',1048575,1048576),('bytes=-77',1048499,1048576),('bytes=1048500-9999999',1048500,1048576),('bytes=-99999999',0,1048576)])
def test_ranges(env,header,start,end):
    _, a, d, _, f = env
    r = d.get(url(link(a,f)),headers={'Range':header})
    assert r.status_code == 206
    assert r.content == f.read_bytes()[start:end]
    assert r.headers['content-range'] == f'bytes {start}-{end-1}/{f.stat().st_size}'
    assert int(r.headers['content-length']) == end-start


@pytest.mark.parametrize('header,status', [('bytes=1048576-',416),('bytes=-0',416),('bytes=garbage',400),('bytes=10-5',400),('things=0-3',400),('bytes=0-1,8-9',200)])
def test_bad_ranges(env,header,status):
    _, a, d, _, f = env
    r = d.get(url(link(a,f)),headers={'Range':header})
    assert r.status_code == status
    if status==416: assert r.headers['content-range'] == 'bytes */1048576'
    if status==200: assert r.content == f.read_bytes()


def test_if_range_and_version(env,monkeypatch):
    _, a, d, _, f = env
    u = url(link(a,f))
    first = d.head(u)
    etag = first.headers['etag']
    assert d.get(u,headers={'Range':'bytes=0-3','If-Range':etag}).status_code == 206
    for value in ('"wrong"','W/'+etag):
        assert d.get(u,headers={'Range':'bytes=0-3','If-Range':value}).status_code == 200
    now = time.time()
    monkeypatch.setattr('app.download.time.time',lambda:now+120)
    date = d.head(u).headers['last-modified']
    assert d.get(u,headers={'Range':'bytes=0-3','If-Range':date}).status_code == 206
    f.write_bytes(b'changed')
    r = d.get(u,headers={'Range':'bytes=0-3','If-Range':etag})
    assert r.status_code == 200 and r.content==b'changed' and r.headers['etag']!=etag


def test_etag_with_restored_mtime(env):
    _,a,d,_,f=env
    u=url(link(a,f));first=d.head(u).headers['etag'];st=f.stat()
    time.sleep(.01)
    with open(f,'r+b') as out:out.write(b'new bytes')
    os.utime(f,ns=(st.st_atime_ns,st.st_mtime_ns))
    assert d.head(u).headers['etag']!=first
    assert d.get(u,headers={'Range':'bytes=0-3','If-Range':first}).status_code==200


def test_parallel_ranges_resume(env):
    _, a, d, _, f = env
    u = url(link(a,f)); data=f.read_bytes(); n=len(data)//8
    def fetch(i):
        return d.get(u,headers={'Range':f'bytes={i*n}-{(i+1)*n-1}'}).content
    with ThreadPoolExecutor(max_workers=8) as pool:
        assert b''.join(pool.map(fetch,range(8))) == data
    prefix=d.get(u,headers={'Range':'bytes=0-12345'}).content
    rest=d.get(u,headers={'Range':f'bytes={len(prefix)}-'}).content
    assert prefix+rest==data


def test_empty_file(env):
    _, a, d, root, _=env
    f=root/'empty';f.touch();u=url(link(a,f))
    assert d.get(u).content==b''
    assert d.get(u,headers={'Range':'bytes=0-'}).status_code==416


def test_large_sparse_file(env):
    _, a, d, root, _=env
    p=root/'huge.bin';size=101*1024**3+7
    with open(p,'wb') as out:
        if os.name=='nt':
            import ctypes,msvcrt
            from ctypes import wintypes
            fn=ctypes.windll.kernel32.DeviceIoControl
            fn.argtypes=[wintypes.HANDLE,wintypes.DWORD,wintypes.LPVOID,wintypes.DWORD,wintypes.LPVOID,wintypes.DWORD,ctypes.POINTER(wintypes.DWORD),wintypes.LPVOID]
            returned=wintypes.DWORD()
            if not fn(msvcrt.get_osfhandle(out.fileno()),0x900c4,None,0,None,0,ctypes.byref(returned),None):
                pytest.skip('当前文件系统不支持 NTFS 稀疏文件')
        out.seek(size-4);out.write(b'tail')
    u=url(link(a,p));h=d.head(u)
    assert int(h.headers['content-length'])==size
    r=d.get(u,headers={'Range':'bytes=-4'})
    assert r.status_code==206 and r.content==b'tail'


def test_folder_and_boundary(env):
    _, a, d, root, f=env
    sub=root/'子目录';sub.mkdir();child=sub/'a.txt';child.write_text('hello')
    u=url(link(a,root))
    r=d.get(u); assert r.status_code==200 and 'text/html' in r.headers['content-type']
    assert str(root) not in r.text and '子目录' in r.text
    assert 'a.txt' in d.get(u,params={'path':'子目录'}).text
    r=d.get(u,params={'path':'子目录/a.txt'},headers={'Range':'bytes=1-3'})
    assert r.status_code==206 and r.content==b'ell'
    for p in ('../outside','子目录/../../outside','/etc/passwd','C:\\Windows\\win.ini','..\\outside','a.txt:secret'):
        assert d.get(u,params={'path':p}).status_code==404
    assert d.get(u+'?path=%2e%2e%2foutside').status_code==404
    for suffix in ('/zip','/download','/batch'):
        assert d.get(u+suffix).status_code==404
    assert d.post(u).status_code==405


def test_replaced_source_denied(env):
    _, a, d, root, f=env
    u=url(link(a,f));f.unlink();f.write_bytes(b'different')
    assert d.get(u).status_code==404


def test_junction_escape(env,tmp_path):
    _, a, d, root, _=env
    target=tmp_path/'outside';target.mkdir();(target/'secret').write_text('secret')
    j=root/'escape'
    if os.name=='nt':
        import subprocess
        result=subprocess.run(['cmd','/c','mklink','/J',str(j),str(target)],capture_output=True)
        if result.returncode:pytest.skip('无法创建测试 Junction')
    else:j.symlink_to(target,target_is_directory=True)
    try:
        u=url(link(a,root))
        assert d.get(u,params={'path':'escape/secret'}).status_code==404
        assert a.post('/api/links',json={'path':str(j/'secret')}).status_code==400
    finally:
        j.rmdir() if os.name=='nt' else j.unlink()
