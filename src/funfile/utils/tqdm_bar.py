import os

from tqdm import tqdm
from .size import file_size


def file_tqdm_bar(path, prefix="", total=None, ncols=120, recursive=False) -> tqdm:
    prefix = f"{prefix}: " if prefix is not None and len(prefix) > 0 else ""
    return tqdm(
        total=total or file_size(path, recursive=recursive),
        desc=f"{prefix}{os.path.basename(path)}"[:20],
        ncols=ncols,
        ascii=True,
        unit="B",
        unit_scale=True,
        unit_divisor=1024,
    )
