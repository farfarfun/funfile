import os
import shutil


def makedirs(path: str | os.PathLike[str]) -> None:
    """递归创建目录，目录已存在时不报错。

    Args:
        path: 要创建的目录路径。
    """
    os.makedirs(path, exist_ok=True)


def delete(path: str | os.PathLike[str]) -> None:
    """删除文件、符号链接或整个目录，路径不存在时忽略。

    Args:
        path: 要删除的路径。
    """
    try:
        if os.path.isdir(path) and not os.path.islink(path):
            shutil.rmtree(path)
        else:
            os.remove(path)
    except FileNotFoundError:
        pass
