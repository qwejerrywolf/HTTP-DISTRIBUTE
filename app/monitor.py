import threading
import time
import uuid


class Monitor:
    def __init__(self, db, limit, geoip=None):
        self.db, self.limit = db, limit
        self.geoip = geoip
        self.active = {}
        self.lock = threading.Lock()

    def start(self, link, filename, ip, method):
        with self.lock:
            if len(self.active) >= self.limit:
                return None
            key = uuid.uuid4().hex
            self.active[key] = dict(id=key, link_id=link['id'], filename=filename, ip=ip, method=method,
                                    started=time.time(), started_mono=time.perf_counter(), bytes=0, status=0, speed=0, tick=time.perf_counter(), tick_bytes=0)
            return key

    def sent(self, key, size):
        with self.lock:
            row = self.active[key]
            row['bytes'] += size
            now = time.perf_counter()
            if now - row['tick'] >= .25:
                row['speed'] = (row['bytes'] - row['tick_bytes']) / (now-row['tick'])
                row['tick'], row['tick_bytes'] = now, row['bytes']

    def stop(self, key):
        with self.lock:
            row = self.active[key]
            if 'duration' not in row:
                row['duration'] = max(0, time.perf_counter() - row['started_mono'])
                row['ended'] = time.time()

    @staticmethod
    def average(row, duration):
        if row['method'] != 'GET' or duration <= 0:
            return None
        if row.get('status') not in (0, 200, 206):
            return None
        return row['bytes'] / duration

    def finish(self, key, status, outcome):
        self.stop(key)
        with self.lock:
            row = self.active.pop(key)
        now = row['ended']
        with self.db.connect() as db:
            db.execute('UPDATE links SET bytes=bytes+?,last_access=? WHERE id=?', (row['bytes'], now, row['link_id']))
            db.execute('INSERT INTO requests(link_id,started,ended,ip,filename,method,status,bytes,outcome,duration) VALUES (?,?,?,?,?,?,?,?,?,?)',
                       (row['link_id'], row['started'], now, row['ip'], row['filename'], row['method'], status, row['bytes'], outcome, row['duration']))
            db.execute("INSERT INTO settings VALUES ('lifetime_bytes',?) ON CONFLICT(key) DO UPDATE SET value=CAST(value AS INTEGER)+CAST(excluded.value AS INTEGER)", (str(row['bytes']),))
            db.execute("INSERT INTO settings VALUES ('lifetime_requests','1') ON CONFLICT(key) DO UPDATE SET value=CAST(value AS INTEGER)+1")

    def snapshot(self):
        with self.lock:
            now = time.perf_counter()
            rows = [dict(r, duration=r.get('duration', max(0, now-r['started_mono'])), speed=r['speed'] if now-r['tick'] < 3 else 0) for r in self.active.values()]
        for row in rows:
            row['average_speed'] = self.average(row, row['duration'])
            row['location'] = self.geoip.lookup(row['ip']) if self.geoip else '属地库未配置'
            for field in ('started_mono', 'tick', 'tick_bytes'):
                row.pop(field, None)
        return dict(active=rows, active_count=len(rows), speed=sum(r['speed'] for r in rows),
                    lifetime_bytes=self.db.setting('lifetime_bytes', 0), requests=self.db.setting('lifetime_requests', 0),
                    active_tokens=len({r['link_id'] for r in rows}), buyer_estimate='不推断唯一买家；IP 可能共享或变化',
                    geoip=self.geoip.status() if self.geoip else {'state':'not_configured','message':'未配置离线 IP 库'})

    def history(self, link_id=None, page=1):
        clause = ' WHERE link_id=?' if link_id is not None else ''
        args = (link_id,) if link_id is not None else ()
        rows = self.db.all('SELECT * FROM requests' + clause + ' ORDER BY id DESC LIMIT 100 OFFSET ?', (*args, (page-1)*100))
        for row in rows:
            row['duration_estimated'] = row['duration'] is None
            if row['duration'] is None:
                row['duration'] = max(0, (row['ended'] or row['started']) - row['started'])
            row['average_speed'] = self.average(row, row['duration'])
            row['location'] = self.geoip.lookup(row['ip']) if self.geoip else '属地库未配置'
        return rows
