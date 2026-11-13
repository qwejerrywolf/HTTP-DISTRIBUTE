import hashlib
import base64
import html
import os
import re
import time
from pathlib import Path
from contextlib import asynccontextmanager
from urllib.parse import quote, urlencode

import anyio
from fastapi import FastAPI, Request, Query
from fastapi.responses import HTMLResponse, PlainTextResponse, Response
from starlette.responses import FileResponse
from starlette.responses import MalformedRangeHeader
from app.filesystem import identity, change_time

FOLDER_SCRIPT = (Path(__file__).parent / 'static' / 'clipboard.js').read_text('utf-8') + "\ndocument.querySelectorAll('[data-copy]').forEach(b=>b.addEventListener('click',async()=>{const i=b.previousElementSibling;if(await copyToClipboard(i.value)){b.textContent='已复制';}else{i.focus();i.select();b.textContent='请按 Ctrl+C 复制';}}));"
FOLDER_CSP = "default-src 'none'; style-src 'unsafe-inline'; script-src 'sha256-" + base64.b64encode(hashlib.sha256(FOLDER_SCRIPT.encode()).digest()).decode() + "'; base-uri 'none'; frame-ancestors 'none'"


def format_file_size(value):
    unit, divisor = ('GB', 1024**3) if value >= 1024**3 else ('MB', 1024**2) if value >= 1024**2 else ('KB', 1024)
    return f'{value / divisor:.2f} {unit}'


class DownloadResponse(FileResponse):
    chunk_size = 256 * 1024

    @classmethod
    def _parse_range_header(cls, value, size):
        # Reject ambiguous numeric syntax accepted by Python int(), e.g. +1 or 1_000.
        if not re.fullmatch(r'bytes=\s*(?:[0-9]+-[0-9]*|-[0-9]+)\s*', value, re.IGNORECASE):
            raise MalformedRangeHeader()
        return super()._parse_range_header(value, size)

    def __init__(self, ctx, link, path, request):
        self.ctx, self.link, self.request = ctx, link, request
        self.file = None
        super().__init__(path, filename=link['display'] if link['kind'] == 'file' and link['display'] else path.name,
                         media_type='application/octet-stream', headers={'Cache-Control': 'private, no-store', 'X-Content-Type-Options': 'nosniff'})

    @asynccontextmanager
    async def _open_file(self):
        yield anyio.wrap_file(self.file)

    def _should_use_range(self, value):
        if value.startswith('W/'):
            return False
        if value == self.headers['etag']:
            return True
        # Second-resolution dates cannot safely validate recently changed files.
        return value == self.headers['last-modified'] and self.stat_result.st_mtime < time.time() - 60 and abs(self.changed_ns - self.stat_result.st_mtime_ns) < 1_000_000_000

    async def __call__(self, scope, receive, send):
        key = self.ctx.monitor.start(self.link, os.path.basename(self.path), self.request.client.host if self.request.client else '', scope['method'])
        if key is None:
            return await PlainTextResponse('下载连接数已达上限，请稍后重试', 503, headers={'Retry-After': '10'})(scope, receive, send)
        status, outcome = 500, 'interrupted'
        try:
            try:
                self.file, st = await anyio.to_thread.run_sync(self.ctx.fs.open_read, self.path)
                if self.link['kind'] == 'file' and identity(st) != self.link['identity']:
                    raise PermissionError('source changed')
            except (OSError, ValueError):
                status, outcome = 404, 'unavailable'
                return await PlainTextResponse('文件不存在或暂时不可访问', 404)(scope, receive, send)
            self.stat_result = st
            self.changed_ns = await anyio.to_thread.run_sync(change_time, self.file, st)
            version = f'{identity(st)}:{st.st_size}:{st.st_mtime_ns}:{self.changed_ns}'
            self.headers['etag'] = '"' + hashlib.sha256(version.encode()).hexdigest() + '"'
            self.set_stat_headers(st)
            # Explicitly ignore multi-range; use independent requests for IDM/aria2.
            headers = list(scope['headers'])
            if scope['method'] == 'HEAD' or any(k.lower() == b'range' and (b',' in v or len(v) > 256) for k, v in headers):
                headers = [(k, v) for k, v in headers if k.lower() != b'range']
            child = dict(scope, headers=headers, extensions={})

            async def measured(message):
                nonlocal status, outcome
                await send(message)
                if message['type'] == 'http.response.start':
                    status = message['status']
                elif message['type'] == 'http.response.body':
                    if status in (200, 206):
                        self.ctx.monitor.sent(key, len(message.get('body', b'')))
                    if not message.get('more_body', False):
                        outcome = 'complete' if status in (200, 206) else 'rejected'

            await super().__call__(child, receive, measured)
        except (OSError, RuntimeError):
            # Never log ASGI scopes, token URLs or local paths on transfer failures.
            outcome = 'interrupted'
            raise ConnectionError('文件传输中断') from None
        finally:
            self.ctx.monitor.stop(key)
            with anyio.CancelScope(shield=True):
                if self.file:
                    await anyio.to_thread.run_sync(self.file.close)
                await anyio.to_thread.run_sync(self.ctx.monitor.finish, key, status, outcome)


def folder_page(ctx, link, token, relative, page):
    root = ctx.fs.path(link['path'])
    p = ctx.fs.share_path(root, relative)
    if not p.is_dir():
        raise ValueError('不是目录')
    result = ctx.fs.browse(p, page=page, limit=100)
    esc = html.escape
    base = '/d/' + quote(token, safe='')
    def url(rel='', n=1):
        return base + '?' + urlencode({'path': rel, 'page': n})
    crumbs = ['<a href="' + esc(url()) + '">分享根目录</a>']
    parts = p.relative_to(root).parts
    for i, part in enumerate(parts):
        crumbs.append('<a href="' + esc(url('/'.join(parts[:i+1]))) + '">' + esc(part) + '</a>')
    rows = []
    for r in result['items']:
        rel = '/'.join((*parts, r['name']))
        href = esc(url(rel))
        label = '浏览' if r['kind'] == 'folder' else '下载文件'
        size = '文件夹' if r['size'] is None else format_file_size(r['size'])
        modified = time.strftime('%Y-%m-%d %H:%M UTC', time.gmtime(r['modified']))
        rows.append(f'<tr><td><a href="{href}">{r["icon_svg"]}{esc(r["name"])}</a></td><td>{size}</td><td>{modified}</td><td><a href="{href}">{label}</a>' + (f' · <input aria-label="文件下载地址" readonly value="{esc(ctx.db.setting("public_url", ctx.config.public_url) + url(rel))}"><button data-copy>复制地址</button>' if r['kind'] == 'file' else '') + '</td></tr>')
    return '<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width"><title>文件分享</title><style>body{font:16px system-ui;background:#f4f6fb;color:#24334b;max-width:1100px;margin:50px auto;padding:24px}a{color:#285be8}table{width:100%;background:white;border-collapse:collapse;margin:24px 0}td,th{padding:16px;text-align:left;border-bottom:1px solid #e8edf4}input{width:160px}nav{display:flex;gap:10px}.file-icon{vertical-align:middle;margin-right:9px}.icon-folder{color:#c79932}.icon-text,.icon-document{color:#5289bb}.icon-code{color:#6974c8}.icon-image{color:#b86bba}.icon-audio{color:#db8b43}.icon-video{color:#8a68d1}.icon-pdf{color:#d36365}.icon-sheet{color:#359775}.icon-slides{color:#d98550}.icon-archive{color:#a58d57}</style><h1>' + esc(link['display'] or root.name) + '</h1><p>只读动态目录 · 仅支持逐个下载文件 · 下载地址可复制到支持断点续传的下载工具</p><nav>' + ' / '.join(crumbs) + '</nav><table><tr><th>名称</th><th>大小 / 类型</th><th>修改时间</th><th>操作</th></tr>' + ''.join(rows) + '</table><nav>' + (f'<a href="{esc(url(relative, page-1))}">上一页</a>' if page > 1 else '') + f'<span>第 {page} 页 · {result["total"]} 项</span>' + (f'<a href="{esc(url(relative, page+1))}">下一页</a>' if page*100 < result['total'] else '') + '</nav></html>'


def create_download(ctx):
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)

    @app.api_route('/d/{token}', methods=['GET', 'HEAD'])
    async def download(token: str, request: Request, path: str = '', page: int = Query(1, ge=1, le=1000000)):
        if ctx.ip_rules.contains(request.client.host if request.client else ''):
            return PlainTextResponse('该 IP 已被屏蔽', 403)
        if len(token) != 43 or any(part.startswith('.httpd-') for part in path.split('/')):
            return PlainTextResponse('链接无效', 404)
        link = await anyio.to_thread.run_sync(ctx.links.authorized, token)
        if not link:
            return PlainTextResponse('链接无效、已过期或源文件不可访问', 404)
        try:
            p = await anyio.to_thread.run_sync(ctx.fs.share_path, link['path'], path) if link['kind'] == 'folder' else await anyio.to_thread.run_sync(ctx.fs.path, link['path'])
            if link['kind'] == 'file' and path:
                return PlainTextResponse('无效路径', 404)
            if await anyio.to_thread.run_sync(p.is_dir):
                page_html = await anyio.to_thread.run_sync(folder_page, ctx, link, token, path, page)
                page_html = page_html.replace('</html>', '<script>' + FOLDER_SCRIPT + '</script></html>')
                await anyio.to_thread.run_sync(ctx.db.execute, 'UPDATE links SET last_access=? WHERE id=?', (time.time(), link['id']))
                headers = {'Cache-Control': 'no-store', 'Content-Security-Policy': FOLDER_CSP, 'Referrer-Policy': 'no-referrer'}
                return Response(headers={**headers, 'Content-Length': str(len(page_html.encode()))}, media_type='text/html') if request.method == 'HEAD' else HTMLResponse(page_html, headers=headers)
            return DownloadResponse(ctx, link, p, request)
        except (OSError, ValueError):
            return PlainTextResponse('文件不存在或无权访问', 404)

    return app
