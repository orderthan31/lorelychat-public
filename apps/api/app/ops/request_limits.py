"""ASGI ingress budget, before JSON/multipart parsing (including chunked bodies)."""
from starlette.responses import JSONResponse


class _BodyLimitExceeded(Exception):
    pass


class RequestBodyLimitMiddleware:
    def __init__(self, app, max_bytes=10 * 1024 * 1024):
        if max_bytes <= 0:
            raise ValueError('max_bytes must be positive')
        self.app, self.max_bytes = app, max_bytes

    async def __call__(self, scope, receive, send):
        if scope['type'] != 'http':
            return await self.app(scope, receive, send)
        async def reject(status=413):
            await JSONResponse({'detail':'Request body exceeds ingress budget'},status_code=status)(scope,receive,send)
        lengths=[value for key,value in scope.get('headers',[]) if key.lower()==b'content-length']
        if lengths:
            try:
                if len(lengths)!=1 or not lengths[0].isdigit():
                    return await reject(400)
                if int(lengths[0]) > self.max_bytes:
                    return await reject()
            except ValueError:
                return await reject(400)
        total=0
        exceeded=False
        started=False
        async def bounded_receive():
            nonlocal total,exceeded
            event=await receive()
            if event['type']=='http.request':
                total+=len(event.get('body',b''))
                if total > self.max_bytes:
                    exceeded=True
                    raise _BodyLimitExceeded()
            return event
        async def guarded_send(event):
            nonlocal started
            if exceeded:
                return  # downstream exception handlers must not replace our 413
            if event['type']=='http.response.start':started=True
            await send(event)
        try:
            await self.app(scope,bounded_receive,guarded_send)
        except _BodyLimitExceeded:
            pass
        if exceeded and not started:
            await reject()
