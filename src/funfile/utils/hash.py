import hashlib
from os import PathLike


def file_hash(file_path: str | PathLike[str], algorithm: str) -> str:
    """
    使用指定算法分块计算文件哈希值。

    Args:
        file_path: 文件路径。
        algorithm: `hashlib` 支持的哈希算法名称。

    Returns:
        小写十六进制哈希值。
    """
    hash_func = hashlib.new(algorithm)
    with open(file_path, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            hash_func.update(chunk)
    return hash_func.hexdigest()


def file_md5(filepath: str | PathLike[str]) -> str:
    """计算文件的 MD5 哈希值。

    Args:
        filepath: 文件路径。
    Returns:
        小写十六进制哈希值。
    """
    return file_hash(filepath, "md5")


def file_sha1(filepath: str | PathLike[str]) -> str:
    """计算文件的 SHA-1 哈希值。参数和返回值同 `file_md5`。"""
    return file_hash(filepath, "sha1")


def file_sha256(filepath: str | PathLike[str]) -> str:
    """计算文件的 SHA-256 哈希值。参数和返回值同 `file_md5`。"""
    return file_hash(filepath, "sha256")


def file_sha512(filepath: str | PathLike[str]) -> str:
    """计算文件的 SHA-512 哈希值。参数和返回值同 `file_md5`。"""
    return file_hash(filepath, "sha512")
