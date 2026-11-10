import threading
import time
import uuid


class Monitor:
    def __init__(self, db, limit):
        self.db, self.limit = db, limit
        self.active = {}
        self.lock = threading.Lock()

    def start(self, link, filename, ip, method):
        with self.lock:
            if len(self.active) >= self.limit:
                return None
            key = uuid.uuid4().hex
            self.active[key] = dict(id=key, link_id=link['id'], filename=filename, ip=ip, method=method,
                                    started=time.time(), bytes=0, status=0, speed=0, tick=time.monotonic(), tick_bytes=0)
            return key

    def sent(self, key, size):
        with self.lock:
            row = self.active[key]
            row['bytes'] += size
            now = time.monotonic()
            if now - row['tick'] >= .25:
                row['speed'] = (row['bytes'] - row['tick_bytes']) / (now-row['tick'])
                row['tick'], row['tick_bytes'] = now, row['bytes']

    def finish(self, key, status, outcome):
        with self.lock:
            row = self.active.pop(key)
        now = time.time()
        with self.db.connect() as db:
            db.execute('UPDATE links SET bytes=bytes+?,last_access=? WHERE id=?', (row['bytes'], now, row['link_id']))
            db.execute('INSERT INTO requests(link_id,started,ended,ip,filename,method,status,bytes,outcome) VALUES (?,?,?,?,?,?,?,?,?)',
                       (row['link_id'], row['started'], now, row['ip'], row['filename'], row['method'], status, row['bytes'], outcome))
            db.execute("INSERT INTO settings VALUES ('lifetime_bytes',?) ON CONFLICT(key) DO UPDATE SET value=CAST(value AS INTEGER)+CAST(excluded.value AS INTEGER)", (str(row['bytes']),))
            db.execute("INSERT INTO settings VALUES ('lifetime_requests','1') ON CONFLICT(key) DO UPDATE SET value=CAST(value AS INTEGER)+1")

    def snapshot(self):
        with self.lock:
            rows = [dict(r, speed=r['speed'] if time.monotonic()-r['tick'] < 3 else 0) for r in self.active.values()]
        return dict(active=rows, active_count=len(rows), speed=sum(r['speed'] for r in rows),
                    lifetime_bytes=self.db.setting('lifetime_bytes', 0), requests=self.db.setting('lifetime_requests', 0),
                    active_tokens=len({r['link_id'] for r in rows}), buyer_estimate='不推断唯一买家；IP 可能共享或变化')
