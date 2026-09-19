import os


def bytes_to_human_readable(size_bytes: float) -> str:
    """
    将字节数转换为人类可读的字符串。

    Args:
        size_bytes: 字节数。

    Returns:
        使用 1024 进制单位的字符串，例如 `1.00KB`。
    """
    if size_bytes == 0:
        return "0B"

    units = ("B", "KB", "MB", "GB", "TB", "PB", "EB", "ZB", "YB")
    unit_index = 0

    while size_bytes >= 1024 and unit_index < len(units) - 1:
        size_bytes /= 1024
        unit_index += 1

    return f"{size_bytes:.2f}{units[unit_index]}"


def file_size(path: str | os.PathLike[str], recursive: bool = False) -> int:
    """返回文件大小，或按需递归累加目录中的文件大小。

    Args:
        path: 文件或目录路径。
        recursive: 是否统计子目录。

    Returns:
        总字节数。

    Raises:
        FileNotFoundError: 路径不存在。
    """
    if os.path.isfile(path):
        return os.path.getsize(path)
    try:
        with os.scandir(path) as entries:
            return sum(
                entry.stat(follow_symlinks=False).st_size
                if entry.is_file(follow_symlinks=False)
                else file_size(entry.path, recursive=True)
                if recursive and entry.is_dir(follow_symlinks=False)
                else 0
                for entry in entries
            )
    except FileNotFoundError:
        raise FileNotFoundError(f"Path not found: {path}") from None
