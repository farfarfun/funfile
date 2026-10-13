import os
import zipfile
from types import TracebackType
from typing import Any, Literal

from funfile.utils import file_size, file_tqdm_bar
from funfile.utils.progress import ProgressReader, ProgressWriter

__all__ = ["ZipFile"]


def _label(member: Any) -> str:
    """取成员的显示名称，兼容 `ZipInfo` 与路径。

    Args:
        member: 成员名称、路径或 `ZipInfo`。

    Returns:
        适合放进进度条描述的字符串。
    """
    if isinstance(member, zipfile.ZipInfo):
        return member.filename
    return os.fspath(member)


class ZipFile(zipfile.ZipFile):
    """在标准库 `ZipFile` 上增加进度显示。

    公开方法保持标准库参数语义，额外接受 `progress` 以复用调用方的进度条。

    进度按**未压缩字节数**统计，而不是像 tar 那样统计归档文件的读取位置：
    zip 的中央目录在文件末尾，读取成员时会在文件里来回定位，底层位置并不单调
    递增，拿它做进度会来回跳。未压缩字节数既单调又能从中央目录预先算出总量。

    进度条是按需创建的——只有真正读写数据的方法才会建，所以 `namelist()`、
    `infolist()` 这类只看元数据的调用不会凭空冒出一个进度条。
    """

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        self._progress = None
        self._progress_owned = False
        super().__init__(*args, **kwargs)

    def _use_progress(self, progress: Any, label: Any, total: int) -> None:
        """登记要使用的进度条，必要时自建或累加总量。

        Args:
            progress: 调用方传入的进度条，为空时自建。
            label: 自建进度条时显示的名称。
            total: 本次操作涉及的未压缩字节数。
        """
        if progress is not None:
            self._progress = progress
            self._progress_owned = False
        elif self._progress is None:
            self._progress = file_tqdm_bar(label, total=total)
            self._progress_owned = True
        elif self._progress_owned:
            # 调用方自带的进度条由调用方维护总量，只累加自建的那个。
            self._progress.total = (self._progress.total or 0) + total
            self._progress.refresh()

    def open(
        self,
        name: Any,
        mode: Literal["r", "w"] = "r",
        pwd: bytes | None = None,
        *,
        force_zip64: bool = False,
    ) -> Any:
        """打开归档成员，在有进度条时包一层进度统计。

        标准库的 `extractall`/`extract`/`write`/`writestr` 内部都通过本方法拿到
        成员流，所以在这里包一次就能让四者都有字节级进度，不必逐个重写。

        Args:
            name: 成员名称或 `ZipInfo`。
            mode: `r` 读取或 `w` 写入。
            pwd: 解密口令。
            force_zip64: 写入时是否强制 ZIP64 扩展。

        Returns:
            成员流；有进度条时为带进度统计的包装对象。
        """
        stream = super().open(name, mode, pwd, force_zip64=force_zip64)
        if self._progress is None:
            return stream
        if mode == "r":
            return ProgressReader(stream, self._progress)
        return ProgressWriter(stream, self._progress)

    def extractall(
        self,
        path: Any = None,
        members: Any = None,
        pwd: bytes | None = None,
        progress: Any = None,
    ) -> None:
        """解压全部或指定成员，并显示按字节统计的进度。

        Args:
            path: 解压目标目录，默认为当前目录。
            members: 要解压的成员；为空时解压全部成员。
            pwd: 解密口令。
            progress: 可选进度条对象。
        Returns:
            None。
        """
        selected = self.infolist() if members is None else [*members]
        self._use_progress(progress, self.filename or "", self._total_size(selected))
        try:
            return super().extractall(path=path, members=selected, pwd=pwd)
        finally:
            self._close_owned_progress()

    def extract(
        self,
        member: Any,
        path: Any = None,
        pwd: bytes | None = None,
        progress: Any = None,
    ) -> str:
        """解压单个成员，并显示按字节统计的进度。

        Args:
            member: 成员名称或 `ZipInfo`。
            path: 解压目标目录，默认为当前目录。
            pwd: 解密口令。
            progress: 可选进度条对象。

        Returns:
            解压后的文件路径。
        """
        self._use_progress(progress, _label(member), self._total_size([member]))
        try:
            return super().extract(member, path=path, pwd=pwd)
        finally:
            self._close_owned_progress()

    def write(
        self,
        filename: str | os.PathLike[str],
        arcname: Any = None,
        compress_type: int | None = None,
        compresslevel: int | None = None,
        progress: Any = None,
    ) -> None:
        """把文件写入归档，并显示按字节统计的进度。

        与 `extractall` 不同，这里不在结束时关闭自建的进度条：写归档通常要连续
        调用多次，总量随调用累加，进度条由 `close()` 统一收尾。

        Args:
            filename: 要写入的文件路径。
            arcname: 归档中的名称；为空时由标准库推导。
            compress_type: 压缩方式。
            compresslevel: 压缩级别。
            progress: 可选进度条对象。
        Returns:
            None。
        """
        self._use_progress(progress, _label(filename), file_size(filename))
        return super().write(
            filename,
            arcname=arcname,
            compress_type=compress_type,
            compresslevel=compresslevel,
        )

    def writestr(
        self,
        zinfo_or_arcname: Any,
        data: Any,
        compress_type: int | None = None,
        compresslevel: int | None = None,
        progress: Any = None,
    ) -> None:
        """把内存中的数据写入归档，并显示按字节统计的进度。

        Args:
            zinfo_or_arcname: 成员名称或 `ZipInfo`。
            data: 要写入的字节或字符串。
            compress_type: 压缩方式。
            compresslevel: 压缩级别。
            progress: 可选进度条对象。
        Returns:
            None。
        """
        # 标准库会把 str 先编码成 UTF-8，总量要按编码后的长度算。
        size = len(data.encode("utf-8") if isinstance(data, str) else data)
        self._use_progress(progress, _label(zinfo_or_arcname), size)
        return super().writestr(
            zinfo_or_arcname,
            data,
            compress_type=compress_type,
            compresslevel=compresslevel,
        )

    def _total_size(self, members: Any) -> int:
        """累加成员的未压缩大小。

        Args:
            members: 成员名称或 `ZipInfo` 的可迭代对象。

        Returns:
            未压缩字节总数。
        """
        total = 0
        for member in members:
            info = (
                member
                if isinstance(member, zipfile.ZipInfo)
                else self.getinfo(os.fspath(member))
            )
            total += info.file_size
        return total

    def _close_owned_progress(self) -> None:
        """只关闭自建的进度条，调用方传入的交还调用方处置。"""
        if self._progress is not None and self._progress_owned:
            self._progress.close()
        self._progress = None
        self._progress_owned = False

    def close(self) -> None:
        """关闭归档及自建的进度条。"""
        try:
            super().close()
        finally:
            self._close_owned_progress()

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: TracebackType | None,
    ) -> None:
        try:
            return super().__exit__(exc_type, exc_val, exc_tb)
        finally:
            self._close_owned_progress()
