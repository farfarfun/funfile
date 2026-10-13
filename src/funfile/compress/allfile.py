import os
import shutil

from funfile.compress import tarfile, zipfile

_TAR_EXTENSIONS = (
    ".tar",
    ".tar.gz",
    ".tgz",
    ".taz",
    ".tar.bz2",
    ".tbz",
    ".tbz2",
    ".tb2",
    ".tar.xz",
    ".txz",
    *tarfile.ZSTD_SUFFIXES,
)


def extractall(
    archive_path: str | os.PathLike[str], path: str | os.PathLike[str] = "."
) -> None:
    """根据扩展名将 zip 或 tar 归档解压到指定目录。

    支持的扩展名见 `_TAR_EXTENSIONS` 与 `.zip`，包含 `.tgz`/`.tbz2` 这类常见缩写。

    Args:
        archive_path: 待解压的归档路径。
        path: 输出目录，默认为当前目录。

    Raises:
        shutil.ReadError: 归档格式不受支持。
        tarfile.CompressionError: 解压 zstd 归档但没有可用的 zstd 后端。
    """
    archive_path = os.fspath(archive_path)
    lower_path = archive_path.lower()
    if lower_path.endswith(".zip"):
        with zipfile.ZipFile(archive_path) as zf:
            zf.extractall(path=path)
    elif lower_path.endswith(_TAR_EXTENSIONS):
        with tarfile.open(archive_path, tarfile.read_mode_for(archive_path)) as tf:
            tf.extractall(path=path)
    else:
        raise shutil.ReadError(f"unsupported archive format: {archive_path}")
