"""Authenticated file response transport with bounded single byte-range reads.

The service's pinned Starlette release predates FileResponse range support.
Authorization and world/source validation happen in the calling route first.
"""
import re
from pathlib import Path
from urllib.parse import quote

from fastapi import Request, Response
from fastapi.responses import StreamingResponse


def private_file_response(path: Path, *, request: Request, media_type: str, filename: str, revision: str) -> Response:
    size = path.stat().st_size
    etag = f'"{revision}"'
    headers = {'Accept-Ranges': 'bytes', 'Cache-Control': 'private, no-store', 'ETag': etag,
               'Content-Disposition': "inline; filename*=UTF-8''" + quote(filename, safe='')}
    start, end, status = 0, size - 1, 200
    value = request.headers.get('range')
    if value and request.headers.get('if-range', etag) == etag:
        match = re.fullmatch(r'bytes=(\d*)-(\d*)', value)
        try:
            if not match or not any(match.groups()) or size == 0:
                raise ValueError
            first, last = match.groups()
            if first:
                start = int(first)
                end = min(size - 1, int(last)) if last else size - 1
            else:
                suffix = int(last)
                if suffix <= 0:
                    raise ValueError
                start = max(0, size - suffix)
            if start >= size or end < start:
                raise ValueError
            status = 206
            headers['Content-Range'] = f'bytes {start}-{end}/{size}'
        except ValueError:
            return Response(status_code=416, headers={**headers, 'Content-Range': f'bytes */{size}'})
    headers['Content-Length'] = str(max(0, end - start + 1))
    if request.method == 'HEAD':
        return Response(status_code=status, headers=headers, media_type=media_type)

    def chunks():
        remaining = end - start + 1
        with path.open('rb') as source:
            source.seek(start)
            while remaining > 0:
                chunk = source.read(min(64 * 1024, remaining))
                if not chunk:
                    break
                remaining -= len(chunk)
                yield chunk
    return StreamingResponse(chunks(), status_code=status, headers=headers, media_type=media_type)
