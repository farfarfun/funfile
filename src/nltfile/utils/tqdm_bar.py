import os

from tqdm import tqdm

from .size import file_size


def file_tqdm_bar(
    path, prefix="", total=None, ncols=None, recursive=False, disable=None
) -> tqdm:
    prefix = f"{prefix}: " if prefix is not None and len(prefix) > 0 else ""
    if total is None and path:
        total = file_size(path, recursive=recursive)
    return tqdm(
        total=total,
        desc=f"{prefix}{os.path.basename(path)}"[:20],
        ncols=ncols,
        dynamic_ncols=ncols is None,
        disable=disable,
        ascii=True,
        unit="B",
        unit_scale=True,
        unit_divisor=1024,
    )
