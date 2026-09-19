import os
import shutil

from funfile.compress import tarfile, zipfile

_TAR_EXTENSIONS = (".tar", ".tar.gz", ".tgz", ".tar.bz2", ".tar.xz", ".txz")


def extractall(
    archive_path: str | os.PathLike[str], path: str | os.PathLike[str] = "."
) -> None:
    """根据扩展名将 zip 或 tar 归档解压到指定目录。

    Args:
        archive_path: 待解压的归档路径。
        path: 输出目录，默认为当前目录。

    Raises:
        shutil.ReadError: 归档格式不受支持。
    """
    archive_path = os.fspath(archive_path)
    lower_path = archive_path.lower()
    if lower_path.endswith(".zip"):
        with zipfile.ZipFile(archive_path) as zf:
            zf.extractall(path=path)
    elif lower_path.endswith(_TAR_EXTENSIONS):
        with tarfile.open(archive_path, "r:*") as tf:
            tf.extractall(path=path)
    else:
        raise shutil.ReadError(f"unsupported archive format: {archive_path}")
