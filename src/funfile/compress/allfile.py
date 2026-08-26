import os
import shutil

from funfile.compress import tarfile, zipfile

_TAR_EXTENSIONS = (".tar", ".tar.gz", ".tgz", ".tar.bz2", ".tar.xz", ".txz")


def extractall(archive_path: str, path: str = "."):
    archive_path = os.fspath(archive_path)
    lower_path = archive_path.lower()
    if lower_path.endswith(".zip"):
        with zipfile.ZipFile(archive_path) as zf:
            zf.extractall(path=path)
    elif lower_path.endswith(_TAR_EXTENSIONS):
        with tarfile.open(archive_path, "r:*") as tf:
            tf.extractall(path=path)
    else:
        raise shutil.ReadError(f"unsupported archive format: {archive_path}")
