from .compress import tarfile, zipfile
from .file import ConcurrentFile
from .utils import (
    bytes_to_human_readable,
    file_hash,
    file_md5,
    file_sha1,
    file_sha256,
    file_sha512,
    file_size,
    file_tqdm_bar,
)

get_size = file_size

__all__ = [
    "ConcurrentFile",
    "bytes_to_human_readable",
    "file_hash",
    "file_md5",
    "file_sha1",
    "file_sha256",
    "file_sha512",
    "file_size",
    "file_tqdm_bar",
    "get_size",
    "tarfile",
    "zipfile",
]
