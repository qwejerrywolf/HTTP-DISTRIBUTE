"""Authenticated uploads and confirmed, snapshot-bound background deletion."""
import os
import secrets
import threading
import time
import uuid
from pathlib import Path

from app.filesystem import identity, within


def signature(p):
    st = p.stat()
    return (identity(st), p.is_dir(), st.st_size if p.is_file() else 0, st.st_mtime_ns)


class FileChanges:
    def __init__(self, ctx):
        self.ctx = ctx
        self.previews = {}
        self.lock = threading.Lock()
        self.upload_slots = threading.BoundedSemaphore(4)
        self.uploads = {}

    def guard_uploads(self, path):
        with self.lock:
            if any(within(parent, path) or path == dst for parent, dst in self.uploads.values()):
                raise ValueError('对象正在接收上传，请等待完成或取消上传后重试')

    def snapshot(self, paths):
        fs = self.ctx.fs
        roots = sorted(set(fs.path(p) for p in paths), key=lambda p: len(p.parts))
        selected = []
        for p in roots:
            if p == p.parent or any(within(root, p) for root in fs.protected):
                raise ValueError('禁止删除磁盘根目录或包含应用数据的目录')
            if any(part.startswith('.httpd-') for part in p.parts):
                raise ValueError('禁止删除正在使用的暂存目录')
            self.ctx.links.guard(p)
            self.guard_uploads(p)
            if not any(within(p, parent) for parent in selected):
                selected.append(p)
        manifest = {}
        def visit(p):
            p = fs.path(p)
            manifest[str(p)] = signature(p)
            if len(manifest) > 100000:
                raise ValueError('一次删除最多 100000 个目录项，请分批选择')
            if p.is_dir():
                with os.scandir(p) as entries:
                    for entry in entries:
                        if entry.name.startswith('.httpd-'):
                            raise ValueError('目录包含暂存任务，请先等待任务完成')
                        visit(Path(entry.path))
        for root in selected:
            visit(root)
        return selected, manifest

    def preview_delete(self, paths, owner):
        with self.ctx.authorize_mutation():
            roots, manifest = self.snapshot(paths)
            token = secrets.token_urlsafe(32)
            with self.lock:
                self.previews = {k: v for k, v in self.previews.items() if v['expires'] > time.time()}
                if len(self.previews) >= 20:
                    raise ValueError('待确认删除过多，请稍后重试')
                self.previews[token] = dict(roots=roots, manifest=manifest, owner=owner, expires=time.time()+600)
            return dict(confirmation=token, items=[self.ctx.fs.info(p) for p in roots],
                        files=sum(not s[1] for s in manifest.values()), folders=sum(s[1] for s in manifest.values()),
                        bytes=sum(s[2] for s in manifest.values()))

    def delete(self, token, owner):
        with self.lock:
            preview = self.previews.get(token)
            if not preview or preview['owner'] != owner or preview['expires'] <= time.time():
                raise ValueError('删除确认已失效，请重新选择并确认')
            ops = self.ctx.operations
            with ops.lock:
                if len(ops.events) >= 20:
                    raise ValueError('操作队列已满')
                key = uuid.uuid4().hex
                event = threading.Event()
                ops.events[key] = event
                self.ctx.db.execute('INSERT INTO operations(id,created,kind,source,destination,status,total) VALUES (?,?,?,?,?,?,?)',
                                    (key, time.time(), 'delete', '\n'.join(map(str, preview['roots'])), '', 'queued', len(preview['manifest'])))
                del self.previews[token]
                ops.pool.submit(self.run_delete, key, preview, event)
        return {'id': key}

    def run_delete(self, key, preview, event):
        ctx = self.ctx
        done = 0
        try:
            with ctx.fs_lock:
                if event.is_set():
                    raise InterruptedError('已取消，未删除任何对象')
                ctx.db.execute("UPDATE operations SET status='running' WHERE id=?", (key,))
                roots, current = self.snapshot(preview['roots'])
                if current != preview['manifest']:
                    raise ValueError('确认后文件或目录已变化，未执行删除；请重新预览确认')
                # All paths are resolved and within the explicitly confirmed roots; remove deepest first.
                ordered = sorted(current, key=lambda s: len(Path(s).parts), reverse=True)
                last_write = 0
                for value in ordered:
                    if event.is_set():
                        raise InterruptedError('已停止剩余删除')
                    p = ctx.fs.path(value)
                    if not any(within(p, root) for root in roots):
                        raise ValueError('删除路径超出确认范围')
                    actual, expected = signature(p), current[value]
                    # Removing children changes their parent's mtime; its identity must still match.
                    if actual[:2] != expected[:2] or (not actual[1] and actual != expected):
                        raise ValueError('删除期间对象变化，已停止')
                    p.rmdir() if actual[1] else p.unlink()
                    done += 1
                    if time.monotonic()-last_write >= .25:
                        ctx.db.execute('UPDATE operations SET done=? WHERE id=?', (done, key))
                        last_write = time.monotonic()
                ctx.db.execute("UPDATE operations SET status='completed',done=? WHERE id=?", (done, key))
        except Exception as exc:
            ctx.db.execute('UPDATE operations SET status=?,done=?,error=? WHERE id=?',
                           ('cancelled' if isinstance(exc, InterruptedError) else 'failed', done,
                            f'{exc}；已删除 {done} 项，已删除内容不可恢复，其他对象保留', key))
        finally:
            with ctx.operations.lock:
                ctx.operations.events.pop(key, None)

    def begin_upload(self, directory, name, total, conflict):
        if not self.upload_slots.acquire(blocking=False):
            raise ValueError('最多同时上传 4 个文件，请稍后重试')
        try:
            return self._begin_upload(directory, name, total, conflict)
        except BaseException:
            self.upload_slots.release()
            raise

    def _begin_upload(self, directory, name, total, conflict):
        if not name or name in ('.', '..') or any(c in name for c in '/\\:*?"<>|\x00') or name.startswith('.httpd-') or name.endswith((' ', '.')):
            raise ValueError('无效文件名，不能包含路径或保留前缀')
        with self.ctx.authorize_mutation():
            parent = self.ctx.fs.path(directory)
            if not parent.is_dir():
                raise ValueError('上传目标必须是目录')
            dst = self.ctx.fs.path(parent / name, False)
            if dst.exists():
                if conflict == 'cancel':
                    raise ValueError('同名对象已存在，请选择自动重命名或覆盖文件')
                if conflict == 'overwrite':
                    if not dst.is_file():
                        raise ValueError('不能覆盖文件夹')
                    self.ctx.links.guard(dst)
                else:
                    base, i = dst, 1
                    while dst.exists():
                        dst = base.with_name(f'{base.stem} ({i}){base.suffix}')
                        i += 1
            key = uuid.uuid4().hex
            stage = parent / ('.httpd-upload-' + key)
            parent_identity = identity(parent.stat())
            original = signature(dst) if dst.exists() else None
            handle = open(stage, 'xb')
            try:
                self.ctx.db.execute('INSERT INTO operations(id,created,kind,source,destination,status,total) VALUES (?,?,?,?,?,?,?)',
                                    (key, time.time(), 'upload', name, str(dst), 'running', total))
            except BaseException:
                handle.close()
                stage.unlink()
                raise
            with self.lock:
                self.uploads[key] = (parent, dst)
            return dict(id=key, file=handle, stage=stage, stage_identity=identity(os.fstat(handle.fileno())), parent=parent,
                        parent_identity=parent_identity, destination=dst, conflict=conflict, done=0,
                        original=original, total=total)

    def release_upload(self, upload):
        with self.lock:
            self.uploads.pop(upload['id'], None)
        self.upload_slots.release()

    def write_upload(self, upload, chunk):
        if upload['done'] + len(chunk) > upload['total']:
            raise ValueError('上传内容超过声明大小')
        upload['file'].write(chunk)
        upload['done'] += len(chunk)

    def commit_upload(self, upload):
        if upload['done'] != upload['total']:
            raise ValueError('上传内容不完整')
        upload['file'].flush()
        os.fsync(upload['file'].fileno())
        upload['file'].close()
        with self.ctx.authorize_mutation():
            parent = self.ctx.fs.path(upload['parent'])
            if identity(parent.stat()) != upload['parent_identity']:
                raise ValueError('上传期间目标目录已变化')
            stage = self.ctx.fs.path(upload['stage'])
            if identity(stage.stat()) != upload['stage_identity']:
                raise ValueError('暂存对象已变化')
            dst = self.ctx.fs.path(upload['destination'], False)
            if (signature(dst) if dst.exists() else None) != upload['original']:
                raise ValueError('上传期间同名目标已变化，未覆盖')
            if dst.exists():
                self.ctx.links.guard(dst)
                os.replace(stage, dst)
            elif os.name == 'nt':
                stage.rename(dst)  # Windows rename never replaces an existing destination.
            else:
                os.link(stage, dst)
                stage.unlink()
            self.ctx.db.execute("UPDATE operations SET status='completed',done=? WHERE id=?", (upload['done'], upload['id']))
            return self.ctx.fs.info(dst)

    def abort_upload(self, upload, message, cancelled=False):
        try:
            upload['file'].close()
        except OSError as exc:
            message += f'；关闭暂存文件失败：{exc}'
        try:
            p = self.ctx.fs.path(upload['stage'])
            if identity(p.stat()) == upload['stage_identity']:
                p.unlink()
        except (OSError, ValueError):
            message += '；暂存文件可能需要人工检查'
        self.ctx.db.execute('UPDATE operations SET status=?,done=?,error=? WHERE id=?',
                            ('cancelled' if cancelled else 'failed', upload['done'], message, upload['id']))
