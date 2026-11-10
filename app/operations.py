import os
import shutil
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from app.filesystem import within, identity


class Cancelled(Exception):
    pass


class Operations:
    def __init__(self, ctx):
        self.ctx = ctx
        self.pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix='file-operation')
        self.events = {}
        self.lock = threading.Lock()

    def submit(self, kind, source='', destination='', conflict='cancel'):
        if kind not in ('copy', 'move', 'rename', 'mkdir', 'size') or conflict not in ('cancel', 'rename', 'overwrite'):
            raise ValueError('无效文件操作')
        with self.lock:
            if len(self.events) >= 20:
                raise ValueError('操作队列已满')
            key = uuid.uuid4().hex
            self.events[key] = threading.Event()
            self.ctx.db.execute('INSERT INTO operations(id,created,kind,source,destination,status) VALUES (?,?,?,?,?,?)',
                                (key, time.time(), kind, source, destination, 'queued'))
            self.pool.submit(self.run, key, kind, source, destination, conflict, self.events[key])
        return {'id': key}

    def cancel(self, key):
        with self.lock:
            if key in self.events:
                self.events[key].set()

    def close(self):
        with self.lock:
            for event in self.events.values():
                event.set()
        self.pool.shutdown(wait=True, cancel_futures=False)

    def run(self, key, kind, source, destination, conflict, event):
        fs, db = self.ctx.fs, self.ctx.db
        stage = None
        stage_owned = False
        done = total = 0
        manifest = {}
        last_write = 0

        def check():
            if event.is_set():
                raise Cancelled('操作已取消，源文件保留')

        def progress(force=False):
            nonlocal last_write
            if force or time.monotonic() - last_write > .25:
                db.execute('UPDATE operations SET done=?,total=? WHERE id=?', (done, total, key))
                last_write = time.monotonic()

        def walk(p):
            check()
            p = fs.path(p)
            if p.is_file():
                yield p
            else:
                with os.scandir(p) as entries:
                    for entry in entries:
                        yield from walk(Path(entry.path))

        def copy_file(src, dst):
            nonlocal done, stage_owned
            check()
            f, before = fs.open_read(src)
            with f, open(dst, 'xb') as out:
                if dst == stage:
                    stage_owned = True
                while chunk := f.read(1024 * 1024):
                    check()
                    out.write(chunk)
                    done += len(chunk)
                    progress()
                out.flush()
                os.fsync(out.fileno())
                after = os.fstat(f.fileno())
                if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
                    raise ValueError('复制期间源文件变化')
            shutil.copystat(src, dst, follow_symlinks=False)

        def copy_tree(src, dst):
            nonlocal stage_owned
            check()
            src = fs.path(src)
            if src.is_file():
                copy_file(src, dst)
            else:
                dst.mkdir()
                if dst == stage:
                    stage_owned = True
                with os.scandir(src) as entries:
                    for entry in entries:
                        copy_tree(Path(entry.path), dst / entry.name)

        try:
            db.execute("UPDATE operations SET status='running' WHERE id=?", (key,))
            with self.ctx.fs_lock:
                check()
                if kind == 'mkdir':
                    dst = fs.path(destination, False)
                    self.ctx.links.guard(dst)
                    dst.mkdir()
                else:
                    src = fs.path(source)
                    self.ctx.file_changes.guard_uploads(src)
                    if src == src.parent or any(within(root, src) for root in fs.protected):
                        raise ValueError('禁止操作磁盘根目录或包含应用数据的目录')
                    if kind == 'size':
                        for p in walk(src):
                            total += p.stat().st_size
                            progress()
                    else:
                        dst = fs.path(destination, False)
                        self.ctx.file_changes.guard_uploads(dst)
                        if src == dst or (src.is_dir() and within(dst, src)):
                            raise ValueError('目标不能是自身或自身子目录')
                        if kind in ('move', 'rename'):
                            self.ctx.links.guard(src)
                        self.ctx.links.guard(dst)
                        if dst.exists():
                            if conflict == 'cancel':
                                raise FileExistsError('目标已存在；请选择自动重命名或覆盖')
                            if conflict == 'rename':
                                base, i = dst, 1
                                while dst.exists():
                                    dst = base.with_name(f'{base.stem} ({i}){base.suffix}')
                                    i += 1
                            elif dst.is_dir() or src.is_dir():
                                raise ValueError('为防止数据丢失，不支持覆盖文件夹；请选择自动重命名')
                        self.ctx.links.guard(dst)
                        db.execute('UPDATE operations SET destination=? WHERE id=?', (str(dst), key))
                        if kind in ('move', 'rename') and src.stat().st_dev == dst.parent.stat().st_dev:
                            check()
                            fs.path(src)
                            fs.path(dst, False)
                            if conflict == 'overwrite':
                                os.replace(src, dst)
                            else:
                                src.rename(dst)
                        else:
                            # Two-pass traversal bounds RAM, rejects reparse points before any copying.
                            for p in walk(src):
                                st = p.stat()
                                manifest[str(p)] = (identity(st), st.st_size, st.st_mtime_ns)
                                total += st.st_size
                                progress()
                            stage = dst.parent / ('.httpd-' + key)
                            copy_tree(src, stage)
                            check()
                            if kind in ('move', 'rename'):
                                current = {}
                                for p in walk(src):
                                    st = p.stat()
                                    current[str(p)] = (identity(st), st.st_size, st.st_mtime_ns)
                                if current != manifest:
                                    raise ValueError('移动期间源目录变化，已保留源文件并取消提交')
                            fs.path(dst, False)
                            if dst.exists() and conflict != 'overwrite':
                                raise FileExistsError('复制期间目标被创建，已停止提交')
                            os.replace(stage, dst) if conflict == 'overwrite' else stage.rename(dst)
                            stage = None
                            # Commit is non-cancellable. A cross-volume move preserves source until copy is complete.
                            if kind in ('move', 'rename'):
                                fs.path(src)
                                shutil.rmtree(src) if src.is_dir() else src.unlink()
                progress(True)
                db.execute("UPDATE operations SET status='completed' WHERE id=?", (key,))
        except Exception as exc:
            db.execute('UPDATE operations SET status=?,error=? WHERE id=?', ('cancelled' if isinstance(exc, Cancelled) else 'failed', str(exc), key))
        finally:
            if stage_owned and stage and stage.exists():
                try:
                    # Only the unique task-owned staging path is removed, never source or user destination.
                    fs.path(stage)
                    shutil.rmtree(stage) if stage.is_dir() else stage.unlink()
                except (OSError, ValueError):
                    db.execute("UPDATE operations SET error=error || '；暂存清理失败，请检查目标目录' WHERE id=?", (key,))
            with self.lock:
                self.events.pop(key, None)
