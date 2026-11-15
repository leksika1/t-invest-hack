"""Bound actual upload bytes, including chunked requests, before multipart parsing."""
from starlette.responses import JSONResponse


class UploadLimitMiddleware:
    def __init__(self, app, max_bytes):
        self.app, self.max_bytes = app, max_bytes

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or scope["method"] != "POST" or not scope["path"].endswith("/documents"):
            return await self.app(scope, receive, send)
        length = dict(scope.get("headers", [])).get(b"content-length")
        if length:
            try:
                if int(length) > self.max_bytes:
                    return await JSONResponse({"detail": {"code": "request_too_large", "message": "Превышен общий лимит загрузки"}}, 413)(scope, receive, send)
            except ValueError:
                return await JSONResponse({"detail": "Некорректный Content-Length"}, 400)(scope, receive, send)
        # Spool the bounded request to disk rather than retaining up to 150 MiB in RAM.
        import tempfile
        with tempfile.TemporaryFile() as spool:
            total = 0
            while True:
                event = await receive()
                if event["type"] == "http.disconnect":
                    return
                body = event.get("body", b"")
                total += len(body)
                if total > self.max_bytes:
                    return await JSONResponse({"detail": {"code": "request_too_large", "message": "Превышен общий лимит загрузки"}}, 413)(scope, receive, send)
                spool.write(body)
                if not event.get("more_body"):
                    break
            spool.seek(0)

            async def replay():
                body = spool.read(64 * 1024)
                return {"type": "http.request", "body": body, "more_body": spool.tell() < total}

            await self.app(scope, replay, send)
