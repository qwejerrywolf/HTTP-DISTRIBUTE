"""Single entry point. Both listeners are supervised in this process."""
import asyncio
import contextlib
import getpass
import logging
from logging.handlers import RotatingFileHandler
import signal
import socket
import sys
import time

import uvicorn
from config import load_config
from app.context import Context
from app.admin import create_admin
from app.download import create_download


class ManagedServer(uvicorn.Server):
    @contextlib.contextmanager
    def capture_signals(self):
        yield


async def serve(ctx):
    log = logging.getLogger('httpd')
    c = ctx.config
    servers = [ManagedServer(uvicorn.Config(app, host=host, port=port, access_log=False, proxy_headers=False,
                    log_config=None, timeout_graceful_shutdown=30, timeout_keep_alive=5))
               for app, host, port in ((create_download(ctx), c.download_host, c.download_port), (create_admin(ctx), c.admin_host, c.admin_port))]
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM, *([signal.SIGBREAK] if sys.platform == 'win32' else [])):
        signal.signal(sig, lambda *_: loop.call_soon_threadsafe(stop.set))
    sockets = []
    try:
        for host, port in ((c.download_host, c.download_port), (c.admin_host, c.admin_port)):
            sock = socket.socket(socket.AF_INET6 if ':' in host else socket.AF_INET, socket.SOCK_STREAM)
            sockets.append(sock)
            if sys.platform == 'win32':
                sock.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
            sock.bind((host, port))
            sock.listen(128)
            sock.setblocking(False)
    except OSError:
        for sock in sockets:
            sock.close()
        log.error('无法绑定服务端口，请检查监听地址、权限或端口占用')
        raise
    tasks = [asyncio.create_task(s.serve(sockets=[sock])) for s, sock in zip(servers, sockets)]
    stopping = asyncio.create_task(stop.wait())

    async def maintenance():
        while True:
            await asyncio.to_thread(ctx.db.execute, 'DELETE FROM requests WHERE started<?', (time.time()-c.history_days*86400,))
            await asyncio.to_thread(ctx.db.execute, "DELETE FROM operations WHERE created<? AND status NOT IN ('queued','running')", (time.time()-c.history_days*86400,))
            await asyncio.to_thread(ctx.db.execute, 'DELETE FROM sessions WHERE expires<?', (time.time(),))
            await asyncio.sleep(3600)

    cleanup = asyncio.create_task(maintenance())
    try:
        done, _ = await asyncio.wait([*tasks, stopping, cleanup], return_when=asyncio.FIRST_COMPLETED)
        if stopping not in done:
            log.error('关键服务意外停止；正在关闭全部监听器')
        for s in servers:
            s.should_exit = True
        await asyncio.gather(*tasks, return_exceptions=True)
        if stopping not in done:
            raise RuntimeError('关键服务停止，请检查日志和端口占用')
    finally:
        stopping.cancel()
        cleanup.cancel()
        await asyncio.gather(stopping, cleanup, return_exceptions=True)
        for sock in sockets:
            sock.close()


def main():
    config = load_config()
    from app.instance import InstanceLock
    instance = InstanceLock(config.data_dir)
    ctx = Context(config)
    handler = RotatingFileHandler(ctx.db.directory / 'server.log', maxBytes=5*1024*1024, backupCount=5, encoding='utf-8')
    handler.setFormatter(logging.Formatter('%(asctime)s %(levelname)s %(message)s'))
    logging.basicConfig(level=logging.INFO, handlers=[handler, logging.StreamHandler()])
    # Uvicorn's exception logger can include full request URLs. Keep only sanitized lifecycle messages.
    logging.getLogger('uvicorn.error').setLevel(logging.CRITICAL)
    try:
        if not ctx.db.one('SELECT id FROM admins LIMIT 1'):
            if not sys.stdin.isatty():
                raise RuntimeError('首次启动必须在交互终端创建管理员：python main.py')
            print('首次初始化：无默认管理员密码。')
            username = input('管理员用户名: ').strip()
            password = getpass.getpass('管理员密码（至少 12 个字符）: ')
            if password != getpass.getpass('再次输入密码: '):
                raise ValueError('两次密码不一致')
            ctx.auth.initialize(username, password)
            del password
        print(f'公网下载监听 {config.download_host}:{config.download_port}')
        print(f'管理界面 http://{config.admin_host}:{config.admin_port} （仅回环/Tailscale）')
        asyncio.run(serve(ctx))
    finally:
        ctx.close()
        instance.close()


if __name__ == '__main__':
    try:
        main()
    except (Exception, KeyboardInterrupt) as exc:
        print(f'服务已停止：{exc}', file=sys.stderr)
        sys.exit(1)
