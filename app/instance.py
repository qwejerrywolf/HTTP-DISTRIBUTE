"""One writer process per data directory; the OS releases this lock on a crash."""
import os
from pathlib import Path


class InstanceLock:
    def __init__(self, directory):
        Path(directory).mkdir(parents=True, exist_ok=True)
        self.file = open(Path(directory) / 'instance.lock', 'a+b')
        self.file.seek(0)
        self.file.write(b'0')
        self.file.flush()
        self.file.seek(0)
        try:
            if os.name == 'nt':
                import msvcrt
                msvcrt.locking(self.file.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(self.file, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            self.file.close()
            raise RuntimeError('此数据目录已有运行中的服务，请勿重复启动') from None

    def close(self):
        self.file.close()
