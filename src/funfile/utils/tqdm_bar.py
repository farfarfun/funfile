import os

from tqdm import tqdm

from .size import file_size


def file_tqdm_bar(
    path: str | os.PathLike[str],
    prefix: str = "",
    total: int | None = None,
    ncols: int | None = None,
    recursive: bool = False,
    disable: bool | None = None,
) -> tqdm:
    """为文件或目录创建按字节统计的进度条。

    Args:
        path: 用于统计大小和显示名称的路径。
        prefix: 进度条描述前缀。
        total: 总字节数；未提供时根据路径计算。
        ncols: 进度条宽度。
        recursive: 计算目录大小时是否递归。
        disable: 是否禁用进度条。

    Returns:
        配置好的 `tqdm` 进度条。
    """
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
