import os

def bytes_to_human_readable(size_bytes):
    """
    将字节数转换为人类可读的字符串形式（如 KB、MB、GB 等）
    """
    if size_bytes == 0:
        return "0B"

    units = ['B', 'KB', 'MB', 'GB', 'TB', 'PB', 'EB', 'ZB', 'YB']
    unit_index = 0

    while size_bytes >= 1024 and unit_index < len(units) - 1:
        size_bytes /= 1024
        unit_index += 1

    return f"{size_bytes:.2f}{units[unit_index]}"

def file_size(path, recursive=False) -> int:
    if not os.path.exists(path):
        raise FileNotFoundError(f"Path not found: {path}")
    if os.path.isfile(path):
        return os.path.getsize(path)
    size = 0
    for filename in os.listdir(path):
        path2 = os.path.join(path, filename)
        if os.path.isfile(path2):
            size += os.path.getsize(path2)
        elif recursive:
            size += file_size(path2, recursive=recursive)
    return size
