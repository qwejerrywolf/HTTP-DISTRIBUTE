import asyncio

import anyio
from starlette.responses import PlainTextResponse


class DownloadGuard:
    """Cancel active ASGI responses, including sends blocked by a slow receiver."""
    def __init__(self, app, ctx):
        self.app, self.ctx = app, ctx

    async def __call__(self, scope, receive, send):
        if scope['type'] != 'http':
            return await self.app(scope, receive, send)
        ip = (scope.get('client') or ('', 0))[0]
        rules = self.ctx.ip_rules
        if rules.status()['enabled']:
            await anyio.to_thread.run_sync(self.ctx.geoip.lookup, ip)
        loop = asyncio.get_running_loop()
        with anyio.CancelScope() as cancel_scope:
            ticket = rules.attach(ip, lambda: loop.call_soon_threadsafe(cancel_scope.cancel))
            if ticket is None:
                return await PlainTextResponse('该 IP 已被屏蔽', 403)(scope, receive, send)
            try:
                await self.app(scope, receive, send)
            finally:
                rules.detach(ticket)
        if cancel_scope.cancel_called:
            # Raising after headers closes the response instead of completing a truncated file.
            raise ConnectionError('自动 IP 屏蔽已中断下载连接')
