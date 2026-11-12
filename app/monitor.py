import threading
import time
import uuid


# Substring hints only: these labels do not prove abuse or trigger IP blocking.
IP_FLAG_KEYWORDS = tuple(word.casefold() for word in (
    'IDC', '数据中心', '数据中心节点', '云服务器', '云计算', '云主机', '云服务',
    '服务器托管', '主机托管', '机房', 'IDC机房', 'BGP机房', 'BGP节点', '弹性计算',
    'datacenter', 'data center', 'hosting', 'hosted', 'cloud computing', 'cloud hosting',
    'cloud server', 'virtual private server', 'vps', 'colocation', 'co-location',
    'dedicated server', 'server hosting', 'compute instance',
    '阿里云', '阿里巴巴', 'Alibaba Cloud', 'Aliyun', '腾讯云', 'Tencent Cloud',
    'Tencent Computing', '华为云', 'Huawei Cloud', '天翼云', 'CTYUN', '移动云',
    'Ecloud', '联通云', '沃云', '百度智能云', 'Baidu Cloud', '火山引擎',
    'Volcengine', '京东云', 'JD Cloud', '金山云', 'Kingsoft Cloud', '优刻得',
    'UCloud', '青云', 'QingCloud', '浪潮云', 'Inspur Cloud',
    'Amazon Web Services', 'Amazon Technologies', 'AWS', 'Microsoft Azure',
    'Microsoft Corporation', 'Google Cloud', 'Google LLC', 'Oracle Cloud',
    'Oracle Corporation', 'DigitalOcean', 'Vultr', 'Linode', 'Akamai Connected Cloud',
    'Hetzner', 'OVH', 'OVHcloud', 'Contabo', 'Leaseweb', 'Scaleway', 'IONOS',
    'Rackspace', 'Cloudflare', 'Akamai', 'Fastly',
    'BGP', '骨干网', '专线', '企业网络', '网络科技', '信息技术', '通信技术',
    '互联网服务', 'CDN', '边缘节点', 'edge', 'network', 'internet services',
    'infrastructure', 'telecom', 'broadband', 'proxy', 'vpn',
))


class Monitor:
    def __init__(self, db, limit, geoip=None, ip_rules=None):
        self.db, self.limit = db, limit
        self.geoip = geoip
        self.ip_rules = ip_rules
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
            row['flagged'] = self.flagged(row['location'])
            row['blocked'] = self.ip_rules.contains(row['ip']) if self.ip_rules else False
            for field in ('started_mono', 'tick', 'tick_bytes'):
                row.pop(field, None)
        return dict(active=rows, active_count=len(rows), speed=sum(r['speed'] for r in rows),
                    lifetime_bytes=self.db.setting('lifetime_bytes', 0), requests=self.db.setting('lifetime_requests', 0),
                    active_tokens=len({r['link_id'] for r in rows}), buyer_estimate='不推断唯一买家；IP 可能共享或变化',
                    geoip=self.geoip.status() if self.geoip else {'state':'not_configured','message':'未配置离线 IP 库'})

    @staticmethod
    def flagged(location):
        text = location.casefold()
        return any(keyword in text for keyword in IP_FLAG_KEYWORDS)

    def history(self, link_id=None, page=1, start=None, end=None, ip='', location='', details=False):
        if start is not None and end is not None and start >= end:
            raise ValueError('结束日期必须晚于开始日期')
        clauses, args = [], []
        for sql, value in [('r.link_id=?', link_id), ('r.started>=?', start), ('r.started<?', end)]:
            if value is not None:
                clauses.append(sql)
                args.append(value)
        if ip:
            clauses.append('instr(lower(r.ip),lower(?))>0')
            args.append(ip.strip())
        if location:
            clauses.append("instr(lower(COALESCE(g.location,'')),lower(?))>0")
            args.append(location.strip())
        source = ' FROM requests r LEFT JOIN ip_locations g ON g.ip=r.ip'
        where = (' WHERE ' + ' AND '.join(clauses)) if clauses else ''
        rows = self.db.all('SELECT r.*,g.location AS saved_location' + source + where + ' ORDER BY r.id DESC LIMIT 100 OFFSET ?', (*args, (page-1)*100))
        for row in rows:
            row['duration_estimated'] = row['duration'] is None
            if row['duration'] is None:
                row['duration'] = max(0, (row['ended'] or row['started']) - row['started'])
            row['average_speed'] = self.average(row, row['duration'])
            row['location'] = self.geoip.lookup(row['ip']) if self.geoip else row['saved_location'] or '属地未知'
            row.pop('saved_location', None)
            row['flagged'] = self.flagged(row['location'])
            row['blocked'] = self.ip_rules.contains(row['ip']) if self.ip_rules else False
        if self.geoip:
            self.geoip.start_backfill()
        if not details:
            return rows
        total = self.db.one('SELECT COUNT(*) AS n' + source + where, args)['n']
        missing = self.db.one('SELECT COUNT(DISTINCT r.ip) AS n' + source + ' WHERE g.ip IS NULL')['n']
        return dict(items=rows, total=total, unresolved_ips=missing)
