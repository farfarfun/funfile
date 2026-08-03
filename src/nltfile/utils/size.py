import os


def bytes_to_human_readable(size_bytes):
    """
    将字节数转换为人类可读的字符串形式（如 KB、MB、GB 等）
    """
    if size_bytes == 0:
        return "0B"

    units = ("B", "KB", "MB", "GB", "TB", "PB", "EB", "ZB", "YB")
    unit_index = 0

    while size_bytes >= 1024 and unit_index < len(units) - 1:
        size_bytes /= 1024
        unit_index += 1

    return f"{size_bytes:.2f}{units[unit_index]}"


def file_size(path, recursive=False) -> int:
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
