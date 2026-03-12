import hashlib


def file_hash(file_path, algorithm):
    """
    计算文件的哈希值
    :param file_path: 文件路径
    :param algorithm: 哈希算法（'md5', 'sha1', 'sha256', 'sha512'）
    :return: 文件的哈希值
    """
    hash_func = hashlib.new(algorithm)
    with open(file_path, "rb") as f:
        chunk = f.read(8192)
        while chunk:
            hash_func.update(chunk)
            chunk = f.read(8192)
    return hash_func.hexdigest()


def file_md5(filepath):
    return file_hash(filepath, "md5")


def file_sha1(filepath):
    return file_hash(filepath, "sha1")


def file_sha256(filepath):
    return file_hash(filepath, "sha256")


def file_sha512(filepath):
    return file_hash(filepath, "sha512")
