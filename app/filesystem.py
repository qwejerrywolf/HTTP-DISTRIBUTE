import ctypes
import os
import shutil
import stat
from pathlib import Path
from app.file_types import icon_svg


def identity(st):
    return f'{st.st_dev}:{st.st_ino}:{getattr(st, "st_birthtime_ns", 0)}'


def change_time(file, st):
    """NTFS ChangeTime survives restoring LastWriteTime; Windows st_ctime is creation time."""
    if os.name != 'nt':
        return st.st_ctime_ns
    import msvcrt
    from ctypes import wintypes
    class BasicInfo(ctypes.Structure):
        _fields_ = [('creation', ctypes.c_longlong), ('access', ctypes.c_longlong),
                    ('write', ctypes.c_longlong), ('change', ctypes.c_longlong), ('attributes', wintypes.DWORD)]
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    fn = kernel.GetFileInformationByHandleEx
    fn.argtypes = [wintypes.HANDLE, ctypes.c_int, wintypes.LPVOID, wintypes.DWORD]
    info = BasicInfo()
    if not fn(msvcrt.get_osfhandle(file.fileno()), 0, ctypes.byref(info), ctypes.sizeof(info)):
        raise ctypes.WinError(ctypes.get_last_error())
    return (info.change - 116444736000000000) * 100


def within(path, root):
    return path == root or root in path.parents


class Filesystem:
    def __init__(self, protected):
        self.protected = [Path(p).resolve() for p in protected]

    def path(self, value, must_exist=True):
        text = str(value)
        if '\x00' in text or text.startswith(('\\\\', '//')):
            raise ValueError('不允许网络路径或设备路径')
        p = Path(text)
        if not p.is_absolute() or '..' in p.parts:
            raise ValueError('需要不含 .. 的本地绝对路径')
        if os.name == 'nt':
            for part in p.parts[1:]:
                reserved = part.split('.')[0].upper() in {'CON', 'PRN', 'AUX', 'NUL', *[f'COM{i}' for i in range(10)], *[f'LPT{i}' for i in range(10)]}
                if ':' in part or part.endswith((' ', '.')) or reserved:
                    raise ValueError('不允许 ADS 或非规范 Windows 路径')
        for item in [*reversed(p.parents), p]:
            try:
                st = item.lstat()
            except FileNotFoundError:
                if must_exist or item != p:
                    raise
                continue
            if stat.S_ISLNK(st.st_mode) or getattr(st, 'st_file_attributes', 0) & 0x400:
                raise ValueError('不允许符号链接、Junction 或重解析点')
        resolved = p.resolve(strict=must_exist)
        if any(within(resolved, root) for root in self.protected):
            raise PermissionError('应用和数据目录受保护')
        if must_exist and not (resolved.is_file() or resolved.is_dir()):
            raise ValueError('仅支持普通文件和目录')
        return resolved

    def share_path(self, root, relative):
        if '\\' in relative or ':' in relative or '\x00' in relative:
            raise ValueError('非法子路径')
        sub = Path(relative)
        if sub.is_absolute() or '..' in sub.parts:
            raise ValueError('非法子路径')
        p = self.path(Path(root) / sub)
        if not within(p, Path(root)):
            raise PermissionError('越过授权边界')
        return p

    def drives(self):
        roots = [Path(f'{c}:\\') for c in 'ABCDEFGHIJKLMNOPQRSTUVWXYZ' if ctypes.windll.kernel32.GetLogicalDrives() & (1 << (ord(c) - 65))] if os.name == 'nt' else [Path('/')]
        result = []
        for root in roots:
            try:
                if os.name == 'nt' and ctypes.windll.kernel32.GetDriveTypeW(str(root)) not in (2, 3):
                    continue
                total, used, free = shutil.disk_usage(root)
                label = ctypes.create_unicode_buffer(261)
                if os.name == 'nt':
                    ctypes.windll.kernel32.GetVolumeInformationW(str(root), label, 261, None, None, None, None, 0)
                result.append(dict(path=str(root), name=label.value or str(root), total=total, free=free))
            except OSError:
                pass
        return result

    def info(self, p):
        st = p.stat()
        return dict(name=p.name or str(p), path=str(p), kind='folder' if p.is_dir() else 'file',
                    size=st.st_size if p.is_file() else None, modified=st.st_mtime, identity=identity(st),
                    icon_svg=icon_svg(p.name, 'folder' if p.is_dir() else 'file'))

    def browse(self, value, q='', sort='name', descending=False, page=1, limit=100):
        p = self.path(value)
        if not p.is_dir():
            raise ValueError('不是目录')
        rows = []
        skipped = 0
        with os.scandir(p) as entries:
            for e in entries:
                if q.casefold() not in e.name.casefold() or e.name.startswith('.httpd-'):
                    continue
                try:
                    child = self.path(e.path)
                    rows.append(self.info(child))
                except (OSError, ValueError):
                    skipped += 1
        key = sort if sort in ('name', 'kind', 'size', 'modified') else 'name'
        rows.sort(key=lambda r: (r[key] or 0) if key in ('size', 'modified') else r[key].casefold(), reverse=descending)
        return dict(path=str(p), parent=str(p.parent), total=len(rows), skipped=skipped,
                    items=rows[(page-1)*limit:page*limit])

    def open_read(self, p):
        p = self.path(p)
        if os.name == 'nt':
            import msvcrt
            from ctypes import wintypes
            kernel = ctypes.WinDLL('kernel32', use_last_error=True)
            kernel.CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, wintypes.LPVOID, wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
            kernel.CreateFileW.restype = wintypes.HANDLE
            # Permit concurrent readers but deny in-place writes and replacement during transfer.
            h = kernel.CreateFileW(str(p), 0x80000000, 1, None, 3, 0x08000000, None)
            if h == wintypes.HANDLE(-1).value:
                raise ctypes.WinError(ctypes.get_last_error())
            kernel.GetFinalPathNameByHandleW.argtypes = [wintypes.HANDLE, wintypes.LPWSTR, wintypes.DWORD, wintypes.DWORD]
            kernel.CloseHandle.argtypes = [wintypes.HANDLE]
            try:
                buf = ctypes.create_unicode_buffer(32768)
                n = kernel.GetFinalPathNameByHandleW(h, buf, len(buf), 0)
                if not n or n >= len(buf):
                    raise PermissionError('无法验证文件句柄路径')
                actual = buf.value.removeprefix('\\\\?\\')
                if os.path.normcase(actual) != os.path.normcase(str(p)):
                    raise PermissionError('文件路径已变化')
                self.path(p)
                fd = msvcrt.open_osfhandle(h, os.O_RDONLY | os.O_BINARY)
                h = None
                file = os.fdopen(fd, 'rb')
            finally:
                if h is not None:
                    kernel.CloseHandle(h)
        else:
            file = open(p, 'rb')
        try:
            st = os.fstat(file.fileno())
            if not stat.S_ISREG(st.st_mode) or identity(st) != identity(self.path(p).stat()):
                raise PermissionError('文件已变化')
            return file, st
        except BaseException:
            file.close()
            raise
