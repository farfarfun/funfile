from funfile.compress import tarfile, zipfile

_TAR_EXTENSIONS = (".tar", ".tar.gz", ".tgz", ".tar.bz2", ".tar.xz", ".txz")


def extractall(archive_path: str, path: str = "."):
    if archive_path.endswith(".zip"):
        with zipfile.ZipFile(archive_path) as zf:
            zf.extractall(path=path)
    elif archive_path.endswith(_TAR_EXTENSIONS):
        with tarfile.TarFile(archive_path) as tf:
            tf.extractall(path=path)
