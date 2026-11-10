import time
from fastapi.testclient import TestClient
from app.admin import create_admin
from conftest import link
from test_download import url


def test_auth_csrf_and_isolation(env):
    ctx,a,d,root,f=env
    anon=TestClient(create_admin(ctx))
    for p in ('/api/links','/api/drives','/api/monitor','/api/settings','/api/operations','/api/requests'):
        assert anon.get(p).status_code==401
        assert d.get(p).status_code==404
    for p in ('/','/docs','/openapi.json','/api/login','/static/app.js'):
        assert d.get(p).status_code==404
    assert anon.post('/api/login',json={'username':'admin','password':'test-password-only-123'}).status_code==403
    assert a.post('/api/links',json={'path':str(f)},headers={'x-csrf-token':'bad'}).status_code==403
    assert a.cookies.get('httpd_session')
    before=a.cookies.get('httpd_session')
    r=a.post('/api/login',json={'username':'admin','password':'test-password-only-123'})
    assert a.cookies.get('httpd_session')!=before
    assert 'HttpOnly' in r.headers['set-cookie'] and 'SameSite=strict' in r.headers['set-cookie']
    a.headers['x-csrf-token']=r.json()['csrf']; assert a.post('/api/logout').status_code==200
    assert a.get('/api/links').status_code==401
    row=ctx.db.one('SELECT password FROM admins')
    assert row['password'].startswith('$argon2id$')


def test_login_rate_limit(env):
    ctx,a,_,_,_=env
    results=[a.post('/api/login',json={'username':'no','password':'bad'}).status_code for _ in range(6)]
    assert 401 in results and results[-1]==429


def test_links_lifecycle(env):
    ctx,a,d,root,f=env
    r=link(a,f,hours=1,note='订单 alpha',display='新名称.bin');u=url(r)
    assert 3590<r['remaining']<=3600
    assert d.get(u).status_code==200
    assert a.patch('/api/links/'+str(r['id']),json={'expires':time.time()-10}).status_code==200
    assert d.get(u).status_code==404
    renewed=a.patch('/api/links/'+str(r['id']),json={'expires':time.time()+3600}).json()
    assert renewed['url']==r['url'] and d.get(u).status_code==200
    a.patch('/api/links/'+str(r['id']),json={'enabled':False});assert d.get(u).status_code==404
    a.patch('/api/links/'+str(r['id']),json={'enabled':True});assert d.get(u).status_code==200
    a.delete('/api/links/'+str(r['id']));assert d.get(u).status_code==404 and f.exists()


def test_folder_expiry_batch_search(env):
    _,a,d,root,f=env
    r=link(a,root,note='needle');s=link(a,f)
    assert a.get('/api/links',params={'q':'needle','kind':'folder'}).json()['total']==1
    assert a.get('/api/links',params={'q':str(f)}).json()['total']==1
    a.post('/api/links/batch',json={'ids':[r['id'],s['id']],'action':'disable'})
    assert a.get('/api/links',params={'status':'disabled'}).json()['total']==2
    assert d.get(url(r),params={'path':f.name}).status_code==404
    a.patch('/api/links/'+str(r['id']),json={'enabled':True,'expires':time.time()-1})
    assert d.get(url(r),params={'path':f.name}).status_code==404
    a.post('/api/links/batch',json={'ids':[r['id']],'action':'extend','hours':1})
    assert d.get(url(r),params={'path':f.name},headers={'Range':'bytes=0-3'}).status_code==206
    a.post('/api/links/batch',json={'ids':[r['id'],s['id']],'action':'delete'})
    assert a.get('/api/links').json()['total']==0 and f.exists()


def test_stats_and_backup(env):
    ctx,a,d,_,f=env
    r=link(a,f);u=url(r)
    d.get(u,headers={'Range':'bytes=0-99'});d.head(u)
    m=a.get('/api/monitor').json()
    assert m['lifetime_bytes']==100 and m['active_count']==0 and m['requests']==2
    assert a.get('/api/links').json()['items'][0]['bytes']==100
    rows=a.get('/api/requests').json();assert len(rows)==2
    assert 'token' not in str(rows)
    b=a.post('/api/backup').json();assert (ctx.db.directory/b['filename']).exists()
    a.put('/api/settings',json={'public_url':'https://example.test:8443'})
    assert a.get('/api/links').json()['items'][0]['url'].startswith('https://example.test:8443/')
    assert a.put('/api/settings',json={'public_url':'javascript:alert(1)'}).status_code==400


def test_protected_data(env):
    ctx,a,d,_,_=env
    assert a.get('/api/browse',params={'path':str(ctx.db.directory)}).status_code==400
    assert a.post('/api/links',json={'path':str(ctx.db.path)}).status_code==400


def test_public_admin_option_keeps_auth_csrf_and_isolation(env):
    ctx,a,d,root,f=env
    ctx.config.admin_host='0.0.0.0'
    ctx.config.allow_public_admin=True
    ctx.config.validate()
    with TestClient(create_admin(ctx)) as anon:
        assert anon.get('/api/links').status_code==401
        assert anon.post('/api/links',json={'path':str(f)},headers={'x-httpd-request':'1'}).status_code==401
    assert a.get('/api/settings').json()['allow_public_admin'] is True
    assert a.post('/api/links',json={'path':str(f)},headers={'x-csrf-token':'bad'}).status_code==403
    assert d.get('/api/links').status_code==404
