import os
import shutil
from os import PathLike


def copy(
    src: str | PathLike[str],
    dst: str | PathLike[str],
    follow_symlinks: bool = True,
) -> str:
    """复制文件及其权限信息，并返回目标路径。

    Args:
        src: 源文件路径。
        dst: 目标文件或目录路径。
        follow_symlinks: 是否跟随符号链接。

    Returns:
        实际写入的目标路径。
    """
    return shutil.copy(src, os.fspath(dst), follow_symlinks=follow_symlinks)
