import io
import os
import tarfile
from collections.abc import Iterable, Iterator
from types import TracebackType
from typing import Any, ClassVar

from funfile.utils import file_tqdm_bar

# zstd 默认级别。选 12 而不是 zstd 自身的默认值 3，是为了贴近既有 xz 用户在意的
# 压缩比：实测 127.8MB 混合语料下 level=12 得到 1.93x，接近 xz preset=6 的 2.02x，
# 而纯编解码吞吐是后者的 11 倍。详见 doc/rust-feasibility.md 附录 A。
ZSTD_DEFAULT_LEVEL = 12

# `file_entar` 支持的压缩算法及其归档后缀。
CODEC_SUFFIXES = {
    "tar": ".tar",
    "gz": ".tar.gz",
    "bz2": ".tar.bz2",
    "xz": ".tar.xz",
    "zst": ".tar.zst",
}


class ProgressFileIO(io.FileIO):
    """在读取文件时按当前位置更新进度条。

    Args:
        path: 文件路径。
        mode: 文件打开模式。
        progress: 提供 `n` 和 `update()` 的进度条。
    """

    def __init__(
        self,
        path: str | os.PathLike[str],
        mode: str = "r",
        progress: Any = None,
        *args: Any,
        **kwargs: Any,
    ) -> None:
        super().__init__(path, mode, *args, **kwargs)
        self._progress = progress

    def _update_progress(self) -> None:
        current = self.tell()
        if current > self._progress.n:
            self._progress.update(current - self._progress.n)

    def read(self, size: int | None = -1) -> bytes:
        """读取字节并同步进度。"""
        data = super().read(size)
        self._update_progress()
        return data

    def readinto(self, buffer: Any) -> int | None:
        """读取到缓冲区并同步进度。"""
        size = super().readinto(buffer)
        self._update_progress()
        return size


class ReadFileWrapper:
    """为已打开的二进制文件增加解压读取进度。

    `r|*` 流式模式允许传入管道、socket、HTTP 响应体等不可定位的流，标准库对此是
    支持的，因此进度更新不能假设 `tell()` 一定可用：可定位时按绝对位置更新，
    否则退化为累加已读字节数。

    Args:
        fileobj: 可读取的二进制文件对象，不要求支持定位。
        progress: 提供 `n` 和 `update()` 的进度条。
    """

    def __init__(self, fileobj: Any, progress: Any) -> None:
        self._fileobj = fileobj
        self._progress = progress
        self._read_bytes = 0
        self._seekable = self._probe_seekable()

    def _probe_seekable(self) -> bool:
        """探测底层流能否 `tell()`,不可定位时返回 False。"""
        try:
            self._fileobj.tell()
        except (AttributeError, io.UnsupportedOperation, OSError):
            return False
        return True

    def _update_progress(self) -> None:
        current = self._fileobj.tell() if self._seekable else self._read_bytes
        if current > self._progress.n:
            self._progress.update(current - self._progress.n)

    def read(self, size: int = -1) -> bytes:
        """读取字节并同步进度。"""
        data = self._fileobj.read(size)
        self._read_bytes += len(data)
        self._update_progress()
        return data

    def readinto(self, buffer: Any) -> int | None:
        """读取到缓冲区并同步进度。"""
        size = self._fileobj.readinto(buffer)
        self._read_bytes += size or 0
        self._update_progress()
        return size

    def __getattr__(self, name: str) -> Any:
        return getattr(self._fileobj, name)


class FileWrapper:
    """为归档写入过程中的源文件读取增加进度。

    Args:
        fileobj: 可读取的二进制文件对象。
        progress: 提供 `update()` 的进度条，可为空。
    """

    def __init__(self, fileobj: Any, progress: Any) -> None:
        self._fileobj = fileobj
        self._progress = progress

    def read(self, size: int = -1) -> bytes:
        """读取字节并累加进度。"""
        data = self._fileobj.read(size)
        if self._progress is not None:
            self._progress.update(len(data))
        return data

    def readline(self, size: int = -1) -> bytes:
        """读取一行并累加进度。"""
        data = self._fileobj.readline(size)
        if self._progress is not None:
            self._progress.update(len(data))
        return data

    def __getattr__(self, name: str) -> Any:
        return getattr(self._fileobj, name)


def _stream_size(fileobj: Any) -> int | None:
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


def _validate_member(root: str, member: tarfile.TarInfo) -> tarfile.TarInfo:
    """校验单个成员的类型与目标路径，不安全时抛出 `tarfile.ExtractError`。

    Args:
        root: 解压根目录的真实路径。
        member: 待校验的归档成员。

    Returns:
        校验通过的成员本身。

    Raises:
        tarfile.ExtractError: 成员类型不是普通文件/目录，或目标路径越出 `root`。
    """
    if not (member.isfile() or member.isdir()):
        raise tarfile.ExtractError(f"unsafe tar member type: {member.name}")
    target = os.path.realpath(os.path.join(root, member.name))
    try:
        inside_root = os.path.commonpath((root, target)) == root
    except ValueError:
        inside_root = False
    if not inside_root:
        raise tarfile.ExtractError(f"unsafe tar member path: {member.name}")
    return member


def _validated_members(
    path: str | os.PathLike[str],
    members: Iterable[tarfile.TarInfo | str],
    archive: tarfile.TarFile,
) -> Iterator[tarfile.TarInfo]:
    """逐个校验并产出归档成员。

    必须保持惰性：对 `r|*` 这类不可回退的流式归档，提前把成员读完会把数据流
    推到末尾，随后解压需要回退定位时就会抛 `tarfile.StreamError`。

    Args:
        path: 解压目标目录。
        members: 成员对象或成员名称的可迭代对象。
        archive: 成员所属的归档，用于按名称解析成员。

    Yields:
        校验通过的成员。
    """
    root = os.path.realpath(path)
    for member in members:
        tarinfo = archive.getmember(member) if isinstance(member, str) else member
        yield _validate_member(root, tarinfo)


class TarFile(tarfile.TarFile):
    """在标准库 `TarFile` 上增加进度显示、安全解压和 zstd 支持。

    公开方法保持标准库参数语义，并额外支持压缩/解压进度显示。
    """

    # 在标准库的压缩类型之外注册 zstd。这里新建字典而不是原地修改，
    # 避免污染 `tarfile.TarFile.OPEN_METH` 影响其他使用标准库的代码。
    OPEN_METH: ClassVar[dict[str, str]] = {
        **tarfile.TarFile.OPEN_METH,
        "zst": "zstopen",
    }

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        self._progress = None
        self._progress_stream = None
        super().__init__(*args, **kwargs)

    @classmethod
    def open(
        cls,
        name: Any = None,
        mode: str = "r",
        fileobj: Any = None,
        bufsize: int = tarfile.RECORDSIZE,
        **kwargs: Any,
    ) -> "TarFile":
        """打开 tar 归档，读取时自动跟踪进度。

        Args:
            name: 归档路径。
            mode: tar 打开模式。
            fileobj: 可选的已打开二进制文件对象。
            bufsize: 流式处理的块大小。
            **kwargs: 传给标准库 `TarFile.open()` 的其他参数。

        Returns:
            已打开的归档对象。
        """
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
            opened = super().open(  # type: ignore[call-overload]
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

    @classmethod
    def zstopen(
        cls,
        name: Any,
        mode: str = "r",
        fileobj: Any = None,
        level: int | None = None,
        threads: int = -1,
        **kwargs: Any,
    ) -> "TarFile":
        """打开 zstd 压缩的 tar 归档，与标准库 `xzopen` 同构。

        已通过 `OPEN_METH` 注册为 `zst`，因此 `r:zst`、`w:zst`、`x:zst` 以及
        `r:*` 自动探测都会调用到这里，无需直接使用本方法。

        不支持 `|` 流式模式：标准库 `_Stream` 把压缩类型硬编码为 gz/bz2/xz，
        无法从子类扩展，`w|zst` 会由标准库抛 `CompressionError`。

        Args:
            name: 归档路径。
            mode: 打开模式，取 `r`、`w` 或 `x`。
            fileobj: 可选的已打开二进制文件对象。
            level: zstd 压缩级别，为空时取 `ZSTD_DEFAULT_LEVEL`。
            threads: 压缩线程数，`-1` 表示使用全部核心，`0` 表示单线程。

        Returns:
            已打开的归档对象。

        Raises:
            ValueError: `mode` 不是 `r`、`w` 或 `x`。
            tarfile.CompressionError: 未安装 `zstandard`。
            tarfile.ReadError: 读取时数据不是 zstd 格式。
        """
        if mode not in ("r", "w", "x"):
            raise ValueError("mode must be 'r', 'w' or 'x'")

        try:
            import zstandard
        except ImportError:
            raise tarfile.CompressionError(
                "zstandard module is not available; install funfile[zstd]"
            ) from None

        if mode == "r":
            compressed = zstandard.open(fileobj or name, "rb")
        else:
            cctx = zstandard.ZstdCompressor(
                level=ZSTD_DEFAULT_LEVEL if level is None else level,
                threads=threads,
            )
            compressed = zstandard.open(fileobj or name, f"{mode}b", cctx=cctx)

        try:
            archive = cls.taropen(name, mode, compressed, **kwargs)
        except (zstandard.ZstdError, EOFError) as exc:
            compressed.close()
            if mode == "r":
                raise tarfile.ReadError("not a zstd file") from exc
            raise
        except Exception:
            compressed.close()
            raise
        archive._extfileobj = False
        return archive

    def addfile(self, tarinfo: tarfile.TarInfo, fileobj: Any = None) -> None:
        """添加成员，并在读取文件对象时更新进度。"""
        if fileobj is not None:
            fileobj = FileWrapper(fileobj, self._progress)
        return super().addfile(tarinfo, fileobj)

    def add(
        self,
        name: str | os.PathLike[str],
        arcname: str | os.PathLike[str] | None = None,
        recursive: bool = True,
        filter: Any = None,
        progress: Any = None,
    ) -> None:  # type: ignore[override]
        """将路径加入归档，并显示按字节统计的进度。

        Args:
            name: 要加入的文件或目录。
            arcname: 归档中的名称；为空时使用原路径名称。
            recursive: 是否递归加入目录内容。
            filter: 标准库 tar 成员过滤器。
            progress: 可选进度条对象。
        Returns:
            None。
        """
        if progress is not None:
            self._progress = progress
        elif self._progress is None:
            self._progress = file_tqdm_bar(name, recursive=recursive)
        return super().add(
            name=name, arcname=arcname, recursive=recursive, filter=filter
        )

    def extractall(
        self,
        path: Any = ".",
        members: Iterable[tarfile.TarInfo] | None = None,
        *,
        numeric_owner: bool = False,
        filter: Any = None,
    ) -> None:
        """解压归档，未自定义 filter 时拒绝越界路径和特殊成员。

        成员校验是惰性的，因此 `r|*` 等流式归档同样可用。

        Args:
            path: 解压目标目录。
            members: 要解压的成员；为空时解压全部成员。
            numeric_owner: 是否使用归档中的数字所有者。
            filter: 标准库成员过滤器。
        Returns:
            None。
        """
        if filter is not None:
            return super().extractall(
                path=path,
                members=members,
                numeric_owner=numeric_owner,
                filter=filter,
            )
        validated = _validated_members(path, self if members is None else members, self)
        if hasattr(tarfile, "data_filter"):
            return super().extractall(
                path=path,
                members=validated,
                numeric_owner=numeric_owner,
                filter="data",
            )
        return super().extractall(
            path=path, members=validated, numeric_owner=numeric_owner
        )

    def extract(
        self,
        member: tarfile.TarInfo | str,
        path: Any = "",
        set_attrs: bool = True,
        *,
        numeric_owner: bool = False,
        filter: Any = None,
    ) -> None:
        """解压单个成员，未自定义 filter 时拒绝越界路径和特殊成员。

        Args:
            member: 成员对象或成员名称。
            path: 解压目标目录，默认为当前目录。
            set_attrs: 是否还原文件属性。
            numeric_owner: 是否使用归档中的数字所有者。
            filter: 标准库成员过滤器。
        Returns:
            None。
        """
        if filter is not None:
            return super().extract(
                member,
                path=path,
                set_attrs=set_attrs,
                numeric_owner=numeric_owner,
                filter=filter,
            )
        tarinfo = self.getmember(member) if isinstance(member, str) else member
        _validate_member(os.path.realpath(path or "."), tarinfo)
        if hasattr(tarfile, "data_filter"):
            return super().extract(
                tarinfo,
                path=path,
                set_attrs=set_attrs,
                numeric_owner=numeric_owner,
                filter="data",
            )
        return super().extract(
            tarinfo, path=path, set_attrs=set_attrs, numeric_owner=numeric_owner
        )

    def _close_progress(self) -> None:
        if self._progress_stream is not None:
            self._progress_stream.close()
            self._progress_stream = None
        if self._progress is not None:
            self._progress.close()
            self._progress = None

    def close(self) -> None:
        """关闭归档及其进度条。"""
        try:
            super().close()
        finally:
            self._close_progress()

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: TracebackType | None,
    ) -> None:
        try:
            return super().__exit__(exc_type, exc_val, exc_tb)
        finally:
            self._close_progress()


tar_open = TarFile.open
open = TarFile.open


def file_entar(
    src_path: str | os.PathLike[str],
    dst_path: str | os.PathLike[str] | None = None,
    codec: str = "xz",
) -> str:
    """将文件或目录压缩为 tar 归档。

    默认仍用 `xz`，保证既有调用方拿到的归档路径和格式不变；追求速度时传
    `codec="zst"`。实测 36MB 混合语料端到端：压缩 17.1s 降到 1.1s，解压
    0.27s 降到 0.07s，归档体积大约 5%。解压侧收益小于压缩侧，是因为写盘
    已经成为瓶颈。

    Args:
        src_path: 源文件或目录路径。
        dst_path: 目标归档路径；默认在源路径后加 `codec` 对应的后缀。
        codec: 压缩算法，取 `tar`（不压缩）、`gz`、`bz2`、`xz` 或 `zst`。
            `zst` 需要安装 `funfile[zstd]`。

    Returns:
        目标归档路径。

    Raises:
        ValueError: `codec` 不受支持。
    """
    if codec not in CODEC_SUFFIXES:
        supported = "、".join(CODEC_SUFFIXES)
        raise ValueError(f"unsupported codec: {codec}; 可选 {supported}")
    src_path = os.fspath(src_path)
    dst_path = (
        os.fspath(dst_path)
        if dst_path is not None
        else f"{src_path}{CODEC_SUFFIXES[codec]}"
    )
    mode = "w" if codec == "tar" else f"w:{codec}"
    with tar_open(dst_path, mode) as archive:
        archive.add(src_path, arcname=os.path.basename(src_path))
    return dst_path


def file_detar(
    src_path: str | os.PathLike[str],
    dst_path: str | os.PathLike[str] | None = None,
) -> str:
    """将 tar 归档安全解压到指定目录。

    Args:
        src_path: 归档路径。
        dst_path: 输出目录；默认为归档所在目录。

    Returns:
        输出目录路径。
    """
    src_path = os.fspath(src_path)
    dst_path = (
        os.fspath(dst_path)
        if dst_path is not None
        else os.path.dirname(src_path) or "."
    )
    with tar_open(src_path, "r:*") as archive:
        archive.extractall(path=dst_path)
    return dst_path
