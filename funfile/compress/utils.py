import os

from tqdm import tqdm


def get_size(path, recursive=False) -> int:
    if os.path.isfile(path):
        return os.path.getsize(path)
    size = 0
    for filename in os.listdir(path):
        path2 = os.path.join(path, filename)
        if os.path.isfile(path2):
            size += os.path.getsize(path2)
        elif recursive:
            size += get_size(path2, recursive=recursive)
    return size


def file_tqdm_bar(path, prefix="", total=None, ncols=120, recursive=False) -> tqdm:
    prefix = f"{prefix}: " if prefix is not None and len(prefix) > 0 else ""
    return tqdm(
        total=total or get_size(path, recursive=recursive),
        desc=f"{prefix}{os.path.basename(path)}",
        ncols=ncols,
        ascii=True,
        unit="B",
        unit_scale=True,
        unit_divisor=1024,
    )
