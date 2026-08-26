import io
import os
import tarfile

from funfile.utils import file_tqdm_bar


class ProgressFileIO(io.FileIO):
    def __init__(self, path, mode="r", progress=None, *args, **kwargs):
        super().__init__(path, mode, *args, **kwargs)
        self._progress = progress

    def _update_progress(self):
        current = self.tell()
        if current > self._progress.n:
            self._progress.update(current - self._progress.n)

    def read(self, size=-1):
        data = super().read(size)
        self._update_progress()
        return data

    def readinto(self, buffer):
        size = super().readinto(buffer)
        self._update_progress()
        return size


class ReadFileWrapper:
    def __init__(self, fileobj, progress):
        self._fileobj = fileobj
        self._progress = progress

    def _update_progress(self):
        current = self._fileobj.tell()
        if current > self._progress.n:
            self._progress.update(current - self._progress.n)

    def read(self, size=-1):
        data = self._fileobj.read(size)
        self._update_progress()
        return data

    def readinto(self, buffer):
        size = self._fileobj.readinto(buffer)
        self._update_progress()
        return size

    def __getattr__(self, name):
        return getattr(self._fileobj, name)


class FileWrapper:
    def __init__(self, fileobj, progress):
        self._fileobj = fileobj
        self._progress = progress

    def read(self, size=-1):
        data = self._fileobj.read(size)
        if self._progress is not None:
            self._progress.update(len(data))
        return data

    def readline(self, size=-1):
        data = self._fileobj.readline(size)
        if self._progress is not None:
            self._progress.update(len(data))
        return data

    def __getattr__(self, name):
        return getattr(self._fileobj, name)


def _stream_size(fileobj):
    try:
        return os.fstat(fileobj.fileno()).st_size
    except (AttributeError, io.UnsupportedOperation, OSError):
        try:
            position = fileobj.tell()
            fileobj.seek(0, os.SEEK_END)
            size = fileobj.tell()
            fileobj.seek(position)
            return size
        except (AttributeError, io.UnsupportedOperation, OSError):
            return None


def _validate_members(path, members):
    root = os.path.realpath(path)
    for member in members:
        if not (member.isfile() or member.isdir()):
            raise tarfile.ExtractError(f"unsafe tar member type: {member.name}")
        target = os.path.realpath(os.path.join(root, member.name))
        try:
            inside_root = os.path.commonpath((root, target)) == root
        except ValueError:
            inside_root = False
        if not inside_root:
            raise tarfile.ExtractError(f"unsafe tar member path: {member.name}")
    return members


class TarFile(tarfile.TarFile):
    def __init__(self, *args, **kwargs):
        self._progress = None
        self._progress_stream = None
        super().__init__(*args, **kwargs)

    @classmethod
    def open(
        cls, name=None, mode="r", fileobj=None, bufsize=tarfile.RECORDSIZE, **kwargs
    ):
        progress = None
        progress_stream = None
        if mode.startswith("r"):
            total = os.path.getsize(name) if fileobj is None else _stream_size(fileobj)
            label = name or getattr(fileobj, "name", "")
            progress = file_tqdm_bar(label, prefix="解压", total=total)
            if fileobj is None:
                progress_stream = ProgressFileIO(name, "rb", progress=progress)
                fileobj = progress_stream
            else:
                fileobj = ReadFileWrapper(fileobj, progress)

        try:
            opened = super().open(
                name=name, mode=mode, fileobj=fileobj, bufsize=bufsize, **kwargs
            )
        except Exception:
            if progress_stream is not None:
                progress_stream.close()
            if progress is not None:
                progress.close()
            raise

        opened._progress = progress
        opened._progress_stream = progress_stream
        return opened

    def addfile(self, tarinfo, fileobj=None):
        if fileobj is not None:
            fileobj = FileWrapper(fileobj, self._progress)
        return super().addfile(tarinfo, fileobj)

    def add(self, name, arcname=None, recursive=True, filter=None, progress=None):
        if progress is not None:
            self._progress = progress
        elif self._progress is None:
            self._progress = file_tqdm_bar(name, recursive=recursive)
        return super().add(
            name=name, arcname=arcname, recursive=recursive, filter=filter
        )

    def extractall(self, path=".", members=None, *, numeric_owner=False, filter=None):
        if filter is not None:
            return super().extractall(
                path=path,
                members=members,
                numeric_owner=numeric_owner,
                filter=filter,
            )
        members = self.getmembers() if members is None else list(members)
        kwargs = {"filter": "data"} if hasattr(tarfile, "data_filter") else {}
        return super().extractall(
            path=path,
            members=_validate_members(path, members),
            numeric_owner=numeric_owner,
            **kwargs,
        )

    def _close_progress(self):
        if self._progress_stream is not None:
            self._progress_stream.close()
            self._progress_stream = None
        if self._progress is not None:
            self._progress.close()
            self._progress = None

    def close(self):
        try:
            super().close()
        finally:
            self._close_progress()

    def __exit__(self, exc_type, exc_val, exc_tb):
        try:
            return super().__exit__(exc_type, exc_val, exc_tb)
        finally:
            self._close_progress()


tar_open = TarFile.open
open = TarFile.open


def file_entar(src_path, dst_path=None):
    src_path = os.fspath(src_path)
    dst_path = os.fspath(dst_path) if dst_path is not None else f"{src_path}.tar.xz"
    with tar_open(dst_path, "w:xz") as archive:
        archive.add(src_path, arcname=os.path.basename(src_path))
    return dst_path


def file_detar(src_path, dst_path=None):
    src_path = os.fspath(src_path)
    dst_path = (
        os.fspath(dst_path)
        if dst_path is not None
        else os.path.dirname(src_path) or "."
    )
    with tar_open(src_path, "r:*") as archive:
        archive.extractall(path=dst_path)
    return dst_path
