import ipaddress
import threading
import time


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
