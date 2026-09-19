import pickle
from os import PathLike
from typing import Any


def dump(data: object, path: str | PathLike[str]) -> None:
    """将 Python 对象序列化到文件。

    Args:
        data: 待序列化的对象。
        path: 目标文件路径。
    """
    with open(path, "wb") as fw:
        pickle.dump(data, fw)


def load(path: str | PathLike[str]) -> Any:
    """从文件反序列化可信任的 pickle 数据。

    Args:
        path: pickle 文件路径。

    Returns:
        文件中存储的 Python 对象。
    """
    with open(path, "rb") as fr:
        return pickle.load(fr)


def dumps(obj: object) -> bytes:
    """将 Python 对象序列化为字节串。

    Args:
        obj: 待序列化的对象。

    Returns:
        pickle 格式字节串。
    """
    return pickle.dumps(obj)


def loads(obj: bytes) -> Any:
    """从字节串反序列化可信任的 pickle 数据。

    Args:
        obj: pickle 格式字串。

    Returns:
        反序列化得到的 Python 对象。
    """
    return pickle.loads(obj)
