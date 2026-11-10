import time
import anyio
from starlette.requests import ClientDisconnect
from pathlib import Path
from typing import Literal

from fastapi import APIRouter, Depends, FastAPI, HTTPException, Query, Request, Response
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from config import ROOT
from app.links import public_url


class Login(BaseModel):
    username: str = Field(max_length=128)
    password: str = Field(max_length=1024)


class NewLink(BaseModel):
    path: str = Field(max_length=32768)
    hours: float = Field(24, gt=0, le=87840)
    expires: float | None = None
    note: str = Field('', max_length=2000)
    display: str = Field('', max_length=255, pattern=r'^[^\r\n/\\]*$')


class EditLink(BaseModel):
    note: str | None = Field(None, max_length=2000)
    display: str | None = Field(None, max_length=255, pattern=r'^[^\r\n/\\]*$')
    expires: float | None = None
    enabled: bool | None = None


class Batch(BaseModel):
    ids: list[int] = Field(min_length=1, max_length=500)
    action: Literal['disable', 'delete', 'extend']
    hours: float = Field(24, gt=0, le=87840)


class Operation(BaseModel):
    kind: Literal['copy', 'move', 'rename', 'mkdir', 'size']
    source: str = Field('', max_length=32768)
    destination: str = Field('', max_length=32768)
    conflict: Literal['cancel', 'rename', 'overwrite'] = 'cancel'


class Settings(BaseModel):
    public_url: str = Field(max_length=2048)


class DeletePreview(BaseModel):
    paths: list[str] = Field(min_length=1, max_length=500)


class DeleteConfirmed(BaseModel):
    confirmation: str = Field(min_length=1, max_length=100)


def create_admin(ctx):
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)

    @app.middleware('http')
    async def secure(request, call_next):
        # Custom same-origin header also prevents login CSRF (no permissive CORS).
        if request.method not in ('GET', 'HEAD', 'OPTIONS') and request.headers.get('x-httpd-request') != '1':
            return JSONResponse({'detail': '缺少同源请求标识'}, 403)
        response = await call_next(request)
        response.headers.update({'Cache-Control': 'no-store', 'X-Content-Type-Options': 'nosniff', 'Referrer-Policy': 'no-referrer',
                                 'Content-Security-Policy': "default-src 'self'; style-src 'self'; script-src 'self'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'"})
        return response

    @app.exception_handler(ValueError)
    async def value_error(request, exc):
        return JSONResponse({'detail': str(exc)}, 400)

    @app.exception_handler(OSError)
    async def os_error(request, exc):
        return JSONResponse({'detail': str(exc)}, 400)

    @app.get('/')
    def index():
        return FileResponse(ROOT / 'app/static/index.html')

    @app.post('/api/login')
    def login(body: Login, request: Request, response: Response):
        token, csrf = ctx.auth.login(body.username, body.password, request.client.host if request.client else '')
        old = request.cookies.get('httpd_session')
        if old:
            ctx.db.execute('DELETE FROM sessions WHERE hash=?', (ctx.auth.digest(old),))
        response.set_cookie('httpd_session', token, max_age=ctx.config.session_hours * 3600, httponly=True, secure=ctx.config.cookie_secure, samesite='strict', path='/')
        return {'csrf': csrf}

    api = APIRouter(prefix='/api', dependencies=[Depends(ctx.auth.require)])

    @api.get('/session')
    def session(request: Request):
        return {'csrf': ctx.auth.require(request)['csrf']}

    @api.post('/logout')
    def logout(request: Request, response: Response):
        ctx.db.execute('DELETE FROM sessions WHERE hash=?', (ctx.auth.digest(request.cookies.get('httpd_session', '')),))
        response.delete_cookie('httpd_session', path='/')
        return {'ok': True}

    @api.get('/drives')
    def drives():
        return ctx.fs.drives()

    @api.get('/browse')
    def browse(path: str, q: str = '', sort: str = 'name', descending: bool = False, page: int = Query(1, ge=1), limit: int = Query(100, ge=1, le=200)):
        return ctx.fs.browse(path, q, sort, descending, page, limit)

    @api.get('/properties')
    def properties(path: str):
        return ctx.fs.info(ctx.fs.path(path))

    @api.post('/files/delete-preview')
    def delete_preview(body: DeletePreview, request: Request):
        return ctx.file_changes.preview_delete(body.paths, ctx.auth.require(request)['hash'])

    @api.post('/files/delete')
    def delete_files(body: DeleteConfirmed, request: Request):
        return ctx.file_changes.delete(body.confirmation, ctx.auth.require(request)['hash'])

    @api.put('/files/upload')
    async def upload_file(request: Request, directory: str, name: str, size: int = Query(ge=0, le=2**63-1),
                          conflict: Literal['cancel', 'rename', 'overwrite'] = 'cancel'):
        upload = await anyio.to_thread.run_sync(ctx.file_changes.begin_upload, directory, name, size, conflict)
        try:
            tick = time.monotonic()
            async for chunk in request.stream():
                await anyio.to_thread.run_sync(ctx.file_changes.write_upload, upload, chunk)
                if time.monotonic() - tick >= .5:
                    await anyio.to_thread.run_sync(ctx.db.execute, 'UPDATE operations SET done=? WHERE id=?', (upload['done'], upload['id']))
                    tick = time.monotonic()
            result = await anyio.to_thread.run_sync(ctx.file_changes.commit_upload, upload)
            return {'id': upload['id'], 'file': result}
        except BaseException as exc:
            with anyio.CancelScope(shield=True):
                await anyio.to_thread.run_sync(ctx.file_changes.abort_upload, upload, str(exc) or '上传已中断', isinstance(exc, (ClientDisconnect, anyio.get_cancelled_exc_class())))
            raise
        finally:
            ctx.file_changes.release_upload(upload)

    @api.post('/operations')
    def operation(body: Operation):
        return ctx.operations.submit(**body.model_dump())

    @api.get('/operations')
    def operations():
        return ctx.db.all('SELECT * FROM operations ORDER BY created DESC LIMIT 100')

    @api.post('/operations/{key}/cancel')
    def cancel(key: str):
        ctx.operations.cancel(key)
        return {'ok': True}

    @api.get('/links')
    def links(q: str = '', status: str = '', kind: str = '', sort: str = 'created', descending: bool = True, page: int = Query(1, ge=1), limit: int = Query(100, ge=1, le=200)):
        return ctx.links.listing(q, status, kind, sort, descending, page, limit)

    @api.post('/links')
    def create_link(body: NewLink):
        return ctx.links.create(**body.model_dump())

    @api.patch('/links/{lid}')
    def edit(lid: int, body: EditLink):
        return ctx.links.update(lid, body.model_dump(exclude_none=True))

    @api.delete('/links/{lid}')
    def delete(lid: int):
        ctx.db.execute('DELETE FROM links WHERE id=?', (lid,))
        return {'ok': True}

    @api.post('/links/batch')
    def batch(body: Batch):
        with ctx.db.connect() as db:
            for lid in set(body.ids):
                if body.action == 'delete':
                    db.execute('DELETE FROM links WHERE id=?', (lid,))
                elif body.action == 'disable':
                    db.execute('UPDATE links SET enabled=0 WHERE id=?', (lid,))
                else:
                    db.execute('UPDATE links SET expires=MAX(expires,?)+? WHERE id=?', (time.time(), body.hours*3600, lid))
        return {'ok': True}

    @api.get('/monitor')
    def monitor():
        return ctx.monitor.snapshot()

    @api.get('/requests')
    def requests(link_id: int | None = None, page: int = Query(1, ge=1)):
        clause = ' WHERE link_id=?' if link_id is not None else ''
        args = (link_id,) if link_id is not None else ()
        return ctx.db.all('SELECT * FROM requests' + clause + ' ORDER BY id DESC LIMIT 100 OFFSET ?', (*args, (page-1)*100))

    @api.get('/settings')
    def settings():
        return dict(public_url=ctx.db.setting('public_url', ctx.config.public_url), download_host=ctx.config.download_host,
                    download_port=ctx.config.download_port, admin_host=ctx.config.admin_host, admin_port=ctx.config.admin_port,
                    cookie_secure=ctx.config.cookie_secure, max_downloads=ctx.config.max_downloads, history_days=ctx.config.history_days,
                    allow_public_admin=ctx.config.allow_public_admin)

    @api.put('/settings')
    def save_settings(body: Settings):
        ctx.db.set_setting('public_url', public_url(body.public_url))
        return {'ok': True}

    @api.post('/backup')
    def backup():
        return {'filename': ctx.db.backup()}

    app.include_router(api)
    app.mount('/static', StaticFiles(directory=ROOT / 'app/static'), name='static')
    return app
