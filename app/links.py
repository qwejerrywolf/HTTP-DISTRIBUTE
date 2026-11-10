import secrets
import time
from pathlib import Path
from urllib.parse import urlsplit

from app.filesystem import identity
from app.file_types import icon_svg

JOIN = 'SELECT l.*,o.path,o.kind,o.identity FROM links l JOIN objects o ON l.object_id=o.id'


def public_url(value):
    value = value.strip()
    if not value or any(c.isspace() or ord(c) < 32 for c in value) or '\\' in value:
        raise ValueError('请输入有效的域名或 IP 前缀')
    if '://' not in value:
        value = 'http://' + value
    u = urlsplit(value)
    if u.scheme not in ('http', 'https') or not u.hostname or u.username or u.password or u.query or u.fragment or u.path not in ('', '/'):
        raise ValueError('公网地址必须为 http(s)://域名[:端口]')
    if u.port is not None and not 1 <= u.port <= 65535:
        raise ValueError('端口必须为 1..65535')
    return value.rstrip('/')


class Links:
    def __init__(self, ctx):
        self.ctx = ctx
        self.db, self.fs = ctx.db, ctx.fs

    def create(self, path, hours=24, expires=None, note='', display=''):
        with self.ctx.authorize_mutation():
            p = self.fs.path(path)
            if any(part.startswith('.httpd-') for part in p.parts):
                raise ValueError('不能分享暂存文件')
            info = self.fs.info(p)
            # Verify readability before creating authorization.
            if info['kind'] == 'file':
                f, _ = self.fs.open_read(p)
                f.close()
            else:
                with __import__('os').scandir(p):
                    pass
            now = time.time()
            expiry = expires if expires is not None else now + hours * 3600
            if not now < expiry <= now + 10 * 366 * 86400:
                raise ValueError('有效期必须在未来，且不超过十年')
            token = secrets.token_urlsafe(32)
            with self.db.connect() as db:
                obj = db.execute('SELECT * FROM objects WHERE path=?', (str(p),)).fetchone()
                if obj and obj['identity'] != info['identity']:
                    if db.execute('SELECT 1 FROM links WHERE object_id=?', (obj['id'],)).fetchone():
                        raise ValueError('此路径的源对象已替换，请先删除旧链接')
                    db.execute('DELETE FROM objects WHERE id=?', (obj['id'],))
                    obj = None
                oid = obj['id'] if obj else db.execute('INSERT INTO objects(path,kind,identity) VALUES (?,?,?)', (str(p), info['kind'], info['identity'])).lastrowid
                lid = db.execute('INSERT INTO links(token,object_id,created,expires,note,display) VALUES (?,?,?,?,?,?)', (token, oid, now, expiry, note, display)).lastrowid
            return self.get(lid)

    def source_ok(self, row):
        try:
            p = self.fs.path(row['path'])
            if identity(p.stat()) != row['identity'] or p.is_dir() != (row['kind'] == 'folder'):
                return False
            if p.is_file():
                handle, _ = self.fs.open_read(p)
                handle.close()
            else:
                with __import__('os').scandir(p):
                    pass
            return True
        except (OSError, ValueError):
            return False

    def decorate(self, row):
        row['name'] = Path(row['path']).name or row['path']
        row['icon_svg'] = icon_svg(row['name'], row['kind'])
        row['status'] = 'disabled' if not row['enabled'] else 'expired' if row['expires'] <= time.time() else 'active' if self.source_ok(row) else 'missing'
        row['url'] = self.db.setting('public_url', self.ctx.config.public_url).rstrip('/') + '/d/' + row.pop('token')
        row['remaining'] = max(0, row['expires'] - time.time())
        return row

    def get(self, lid):
        row = self.db.one(JOIN + ' WHERE l.id=?', (lid,))
        if not row:
            raise ValueError('链接不存在')
        return self.decorate(row)

    def authorized(self, token):
        row = self.db.one(JOIN + ' WHERE l.token=? AND l.enabled=1 AND l.expires>?', (token, time.time()))
        if not row or not self.source_ok(row):
            return None
        return row

    def listing(self, q='', status='', kind='', sort='created', descending=True, page=1, limit=100):
        rows = [self.decorate(r) for r in self.db.all(JOIN)]
        counts = {s: sum(r['status'] == s for r in rows) for s in ('active', 'expired', 'disabled', 'missing')}
        counts['total'] = len(rows)
        rows = [r for r in rows if (not status or status == r['status']) and (not kind or kind == r['kind']) and q.casefold() in (r['name'] + r['path'] + r['note']).casefold()]
        key = sort if sort in ('created', 'expires', 'last_access', 'name', 'bytes') else 'created'
        rows.sort(key=lambda r: r[key] or ('' if key == 'name' else 0), reverse=descending)
        return dict(counts=counts, total=len(rows), items=rows[(page-1)*limit:page*limit])

    def update(self, lid, changes):
        allowed = {k: v for k, v in changes.items() if k in ('note', 'display', 'expires', 'enabled') and v is not None}
        if 'expires' in allowed and not 0 < allowed['expires'] < time.time() + 10 * 366 * 86400:
            raise ValueError('过期时间超出允许范围')
        if allowed:
            self.db.execute('UPDATE links SET ' + ','.join(f'{k}=?' for k in allowed) + ' WHERE id=?', (*allowed.values(), lid))
        return self.get(lid)

    def affected(self, p):
        from app.filesystem import within
        return [r['id'] for r in self.db.all(JOIN) if within(Path(r['path']), p) or within(p, Path(r['path']))]

    def guard(self, p):
        ids = self.affected(p)
        if ids:
            raise ValueError('操作影响现有分享，请先删除关联链接（包括已禁用或过期链接）: ' + ', '.join(map(str, ids)))
