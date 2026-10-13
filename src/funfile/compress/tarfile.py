import io
import os
import tarfile
from collections.abc import Iterable, Iterator
from types import TracebackType
from typing import Any, ClassVar, cast

from funfile.utils import file_size, file_tqdm_bar
from funfile.utils.progress import ProgressReader

# zstd 默认级别。选 12 而不是 zstd 自身的默认值 3，是在压缩比上向 xz 靠：
# 34MB 源码树语料上 level=3 比 xz 大 38%，level=12 收窄到 22%，仍只要 0.9s
# （xz 要 9.7s）。12 而不是 9，是因为两者在源码树上几乎等价（23% vs 22%），
# 但在重复性强的日志语料上 9 还差 47%、12 已到 41%——12 从不更差，有时明显更好，
# 代价是 30MB 量级多半秒。再往上收益递减：level=19 能收窄到 9%，却要 8.8s，
# 已经没必要不用 xz。跑 `python benchmarks/zstd_vs_xz.py` 可复现。
ZSTD_DEFAULT_LEVEL = 12

# `file_entar` 支持的压缩算法及其归档后缀。
CODEC_SUFFIXES = {
    "tar": ".tar",
    "gz": ".tar.gz",
    "bz2": ".tar.bz2",
    "xz": ".tar.xz",
    "zst": ".tar.zst",
}

# zstd 归档的后缀。读取时按后缀显式指定压缩类型而不是交给 `r:*` 自动探测：
# 标准库的探测循环会把 `CompressionError` 吞掉换成泛化的 `ReadError`，
# 没有可用 zstd 后端时的可执行提示就丢了。
ZSTD_SUFFIXES = (".tar.zst", ".tzst")


def read_mode_for(path: str | os.PathLike[str]) -> str:
    """按后缀给出 tar 读取模式。

    zstd 走显式的 `r:zst`（原因见 `ZSTD_SUFFIXES`），其余交给 `r:*` 自动探测。

    Args:
        path: 归档路径。

    Returns:
        可直接传给 `tar_open` 的读取模式。
    """
    return "r:zst" if os.fspath(path).lower().endswith(ZSTD_SUFFIXES) else "r:*"


def _zstd_backend() -> tuple[str, Any]:
    """返回可用的 zstd 后端，优先标准库。

    Python 3.14 起标准库自带 zstd（PEP 784），在这些版本上不装 `funfile[zstd]`
    也能用 zst；更早版本回落到第三方 `zstandard`。两个后端都能设压缩级别和
    线程数，所以 `zstopen` 对外行为与 Python 版本无关。

    Returns:
        `("stdlib", compression.zstd)` 或 `("zstandard", zstandard)`。

    Raises:
        tarfile.CompressionError: 两个后端都不可用。
    """
    try:
        import compression.zstd as stdlib_zstd  # Python 3.14+
    except ImportError:
        pass
    else:
        return "stdlib", stdlib_zstd

    try:
        import zstandard
    except ImportError:
        raise tarfile.CompressionError(
            "zstd support requires Python 3.14+ or the zstandard module; "
            "install funfile[zstd]"
        ) from None
    return "zstandard", zstandard


class ProgressFileIO(io.FileIO):
    """在读取文件时按当前位置更新进度条。

    这里继承 `io.FileIO` 而不是像 `ProgressReader` 那样做代理：标准库 `tarfile`
    会对传入的 `fileobj` 调用 `fileno()`、`seek()` 等真实文件接口，直接继承比
    逐个转发更稳。

    Args:
        path: 文件路径。
        mode: 文件打开模式。
        progress: 提供 `n` 和 `update()` 的进度条，为 `None` 时跳过进度更新。
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
        if self._progress is None:
            return
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
    # Python 3.14 的标准库已自带 `zst` 键，这次合并对它是幂等的；真正生效的是
    # 子类上的 `zstopen` 覆盖，见该方法的说明。
    OPEN_METH: ClassVar[dict[str, str]] = {
        **tarfile.TarFile.OPEN_METH,
        "zst": "zstopen",
    }

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        self._progress = None
        self._progress_stream = None
        self._progress_owned = False
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
                # 整个归档对应一个进度条，按绝对位置计数，定位不会重复累加。
                fileobj = ProgressReader(fileobj, progress, absolute=True)

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
        opened._progress_owned = progress is not None
        return opened

    # Python 3.14 的标准库 `zstopen` 收 `options`/`zstd_dict`，这里收 `threads`，
    # 签名刻意不一致——理由见下面的 docstring，故压掉 override 检查。
    @classmethod
    def zstopen(  # type: ignore[override]
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

        在 Python 3.14 上本方法仍然覆盖标准库的同名实现，目的是让压缩级别默认值
        和 `threads` 参数不随解释器版本变化；底层后端由 `_zstd_backend` 自动选择，
        所以 3.14 不需要装 `funfile[zstd]`。

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
            tarfile.CompressionError: 没有可用的 zstd 后端。
            tarfile.ReadError: 读取时数据不是 zstd 格式。
        """
        if mode not in ("r", "w", "x"):
            raise ValueError("mode must be 'r', 'w' or 'x'")

        kind, backend = _zstd_backend()

        if mode == "r":
            compressed = backend.open(fileobj or name, "rb")
        elif kind == "stdlib":
            # 标准库不接 `level` 与 `options` 并存，级别只能一起放进 options；
            # `nb_workers` 也没有「全部核心」这个取值，-1 要自己翻译。
            params = backend.CompressionParameter
            compressed = backend.open(
                fileobj or name,
                f"{mode}b",
                options={
                    params.compression_level: ZSTD_DEFAULT_LEVEL
                    if level is None
                    else level,
                    params.nb_workers: (os.cpu_count() or 1)
                    if threads < 0
                    else threads,
                },
            )
        else:
            cctx = backend.ZstdCompressor(
                level=ZSTD_DEFAULT_LEVEL if level is None else level,
                threads=threads,
            )
            compressed = backend.open(fileobj or name, f"{mode}b", cctx=cctx)

        try:
            # mode 已在上面收窄到 r/w/x，但类型检查器看不出来。
            archive = cls.taropen(name, cast(Any, mode), compressed, **kwargs)
        except (backend.ZstdError, EOFError) as exc:
            compressed.close()
            if mode == "r":
                raise tarfile.ReadError("not a zstd file") from exc
            raise
        except Exception:
            compressed.close()
            raise
        # 告诉标准库 fileobj 由它负责关闭；`_extfileobj` 不在 typeshed 里。
        archive._extfileobj = False  # type: ignore[attr-defined]
        return archive

    def addfile(self, tarinfo: tarfile.TarInfo, fileobj: Any = None) -> None:
        """添加成员，并在读取文件对象时更新进度。"""
        if fileobj is not None:
            # 多个成员共享同一个进度条，按累加计数，换文件时不会被重置。
            fileobj = ProgressReader(fileobj, self._progress)
        return super().addfile(tarinfo, fileobj)

    def add(
        self,
        name: str | os.PathLike[str],
        arcname: str | os.PathLike[str] | None = None,
        recursive: bool = True,
        filter: Any = None,
        progress: Any = None,
    ) -> None:
        """将路径加入归档，并显示按字节统计的进度。

        对同一个归档多次调用时，自建的进度条会把每次的大小累加进 `total`，
        否则总量只停留在首次调用的路径上，进度会冲过 100%。

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
            self._progress_owned = False
        elif self._progress is None:
            self._progress = file_tqdm_bar(name, recursive=recursive)
            self._progress_owned = True
        elif self._progress_owned:
            # 调用方自带的进度条由调用方维护总量，只累加自建的那个。
            self._progress.total = (self._progress.total or 0) + file_size(
                name, recursive=recursive
            )
            self._progress.refresh()
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
    **kwargs: Any,
) -> str:
    """将文件或目录压缩为 tar 归档。

    默认仍用 `xz`，保证既有调用方拿到的归档路径和格式不变；追求速度时传
    `codec="zst"`，压缩能快一个数量级（34MB 源码树 9.7s → 0.8s）。代价是归档
    更大，而且**大多少强依赖数据的可压缩性**，所以换 codec 前先看自己的数据
    属于哪一类（30MB 量级语料实测，`python benchmarks/zstd_vs_xz.py` 可复现）：

    | 数据 | zst 相对 xz 的体积 |
    | --- | --- |
    | 大量不可压缩内容（已压缩的图片、模型权重） | +9% |
    | 源码树 | +22% |
    | 高度重复的文本（日志、JSON 导出） | +41% |

    Args:
        src_path: 源文件或目录路径。
        dst_path: 目标归档路径；默认在源路径后加 `codec` 对应的后缀。
        codec: 压缩算法，取 `tar`（不压缩）、`gz`、`bz2`、`xz` 或 `zst`。
            `zst` 在 Python 3.14 以下需要安装 `funfile[zstd]`。
        **kwargs: 透传给对应压缩类型的参数，用于调节速度与体积的取舍。
            `zst` 收 `level` 和 `threads`，`xz` 收 `preset`，
            `gz`/`bz2` 收 `compresslevel`。

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
    with tar_open(dst_path, mode, **kwargs) as archive:
        archive.add(src_path, arcname=os.path.basename(src_path))
    return dst_path


def file_detar(
    src_path: str | os.PathLike[str],
    dst_path: str | os.PathLike[str] | None = None,
) -> str:
    """将 tar 归档安全解压到指定目录。

    压缩类型按后缀判断（见 `read_mode_for`），其余交给 `r:*` 自动探测。

    Args:
        src_path: 归档路径。
        dst_path: 输出目录；默认为归档所在目录。

    Returns:
        输出目录路径。

    Raises:
        tarfile.CompressionError: 解压 zstd 归档但没有可用的 zstd 后端。
        tarfile.ReadError: 归档无法识别。
    """
    src_path = os.fspath(src_path)
    dst_path = (
        os.fspath(dst_path)
        if dst_path is not None
        else os.path.dirname(src_path) or "."
    )
    with tar_open(src_path, read_mode_for(src_path)) as archive:
        archive.extractall(path=dst_path)
    return dst_path
