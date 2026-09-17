"""Bounded multipart transport for a single local source-folder handoff."""

from contextlib import asynccontextmanager

from fastapi import HTTPException, Request
from python_multipart.exceptions import MultipartParseError
from starlette.formparsers import MultiPartException, MultiPartParser

from research_intern.workspace.project import MAX_UPLOAD_BYTES, MAX_UPLOAD_FILES, SourceFile

# Allow multipart headers in addition to the source byte limit.
MAX_REQUEST_BYTES = MAX_UPLOAD_BYTES + MAX_UPLOAD_FILES * 1024


class FolderParser(MultiPartParser):
    """Track all opened streams, including a final incomplete part on disconnect."""
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.opened = []
        self.complete = False

    def on_headers_finished(self) -> None:
        super().on_headers_finished()
        if self._current_part.file is not None:
            self.opened.append(self._current_part.file)

    def on_end(self) -> None:
        self.complete = True


@asynccontextmanager
async def folder_files(request: Request):
    if request.headers.get("content-type", "").split(";", 1)[0].strip() != "multipart/form-data":
        raise HTTPException(415, "Select a research folder to upload")

    async def bounded_stream():
        total = 0
        async for chunk in request.stream():
            total += len(chunk)
            if total > MAX_REQUEST_BYTES:
                raise HTTPException(413, "Source upload exceeds the 64 MiB limit")
            yield chunk

    parser = FolderParser(request.headers, bounded_stream(), max_files=MAX_UPLOAD_FILES, max_fields=0)
    try:
        form = await parser.parse()
        if not parser.complete:
            raise HTTPException(400, "The folder upload was interrupted. Please select it again.")
        if any(key != "files" for key, _ in form.multi_items()):
            raise HTTPException(400, "Only research source files are accepted")
        yield [SourceFile(item.filename or "", item.file) for item in form.getlist("files")]
    except (MultiPartException, MultipartParseError) as exc:
        raise HTTPException(400, "Invalid folder upload: " + str(exc)) from exc
    finally:
        for item in parser.opened:
            await item.close()
