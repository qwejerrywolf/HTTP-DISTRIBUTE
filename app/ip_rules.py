import ipaddress
import threading
import time
from app.ip_keywords import flagged


def normalize_ip(value):
    ip = ipaddress.ip_address(value.split('%', 1)[0])
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped:
        ip = ip.ipv4_mapped
    return str(ip)


class IPRules:
    def __init__(self, db):
        self.db = db
        self.lock = threading.Lock()
        self.blocked = {r['ip'] for r in db.all('SELECT ip FROM blocked_ips')}
        self.handled = {r['ip'] for r in db.all('SELECT ip FROM auto_ip_actions')}
        self.enabled = bool(db.setting('auto_block_flagged', False))
        self.connections = {}

    def status(self):
        with self.lock:
            return dict(enabled=self.enabled, handled_count=len(self.handled))

    def set_enabled(self, enabled):
        with self.lock:
            self.db.set_setting('auto_block_flagged', enabled)
            self.enabled = enabled
        if enabled:
            for row in self.db.all('SELECT ip,location FROM ip_locations'):
                self.evaluate(row['ip'], row['location'])

    def evaluate(self, value, location):
        if not flagged(location):
            return False
        try:
            ip = normalize_ip(value)
        except ValueError:
            return False
        with self.lock:
            if not self.enabled or ip in self.handled:
                return False
            # Commit both records together; manual removal never deletes this receipt.
            with self.db.connect() as db:
                inserted = db.execute('INSERT OR IGNORE INTO auto_ip_actions VALUES (?,?,?)', (ip, location, time.time())).rowcount
                if inserted:
                    db.execute('INSERT OR IGNORE INTO blocked_ips VALUES (?,?)', (ip, time.time()))
            self.handled.add(ip)
            if not inserted:
                return False
            self.blocked.add(ip)
            for cancel in self.connections.get(ip, {}).values():
                cancel()
            return True

    def attach(self, value, cancel):
        try:
            ip = normalize_ip(value)
        except ValueError:
            return ('', object())
        key = object()
        with self.lock:
            if ip in self.blocked:
                return None
            self.connections.setdefault(ip, {})[key] = cancel
        return ip, key

    def detach(self, ticket):
        if ticket is None:
            return
        ip, key = ticket
        with self.lock:
            group = self.connections.get(ip, {})
            group.pop(key, None)
            if not group:
                self.connections.pop(ip, None)

    def active_ips(self):
        with self.lock:
            return list(self.connections)

    def contains(self, value):
        try:
            ip = normalize_ip(value)
        except ValueError:
            return False
        with self.lock:
            return ip in self.blocked

    def change(self, value, blocked):
        ip = normalize_ip(value.strip())
        with self.lock:
            if blocked:
                self.db.execute('INSERT OR IGNORE INTO blocked_ips(ip,created) VALUES (?,?)', (ip, time.time()))
                self.blocked.add(ip)
            else:
                self.db.execute('DELETE FROM blocked_ips WHERE ip=?', (ip,))
                self.blocked.discard(ip)
        return ip
