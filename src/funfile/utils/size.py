import os


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
