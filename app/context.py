import threading
from contextlib import contextmanager
from config import ROOT
from app.database import Database
from app.filesystem import Filesystem
from app.auth import Auth
from app.links import Links, public_url
from app.monitor import Monitor
from app.geolocation import GeoLocator
from app.ip_rules import IPRules


class Context:
    def __init__(self, config):
        self.config = config
        config.public_url = public_url(config.public_url)
        self.db = Database(config.data_dir)
        self.fs = Filesystem([ROOT, config.data_dir])
        self.fs_lock = threading.RLock()
        self.auth = Auth(self.db, config)
        self.links = Links(self)
        self.ip_rules = IPRules(self.db)
        self.geoip = GeoLocator(config.geoip_enabled, self.db, self.ip_rules.evaluate)
        self.monitor = Monitor(self.db, config.max_downloads, self.geoip, self.ip_rules)
        from app.operations import Operations
        self.operations = Operations(self)
        from app.file_changes import FileChanges
        self.file_changes = FileChanges(self)

    def set_auto_block(self, enabled):
        self.ip_rules.set_enabled(enabled)
        if enabled:
            for ip in self.ip_rules.active_ips():
                self.geoip.lookup(ip)
            self.geoip.start_backfill()
        return self.ip_rules.status()

    def close(self):
        self.operations.close()
        self.geoip.close()

    @contextmanager
    def authorize_mutation(self):
        # Never leave an HTTP worker waiting for a multi-hour copy task.
        if not self.fs_lock.acquire(blocking=False):
            raise ValueError('文件任务正在操作磁盘，请完成或取消任务后重试')
        try:
            yield
        finally:
            self.fs_lock.release()
