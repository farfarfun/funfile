from funfile.compress import zipfile, tarfile


def extractall(archive_path: str, path: str = "."):
    if archive_path.endswith(".zip"):
        with zipfile.ZipFile(archive_path) as zf:
            zf.extractall(path=path)
    elif archive_path.endswith((".tar", ".gz", ".tz")):
        with tarfile.TarFile(archive_path) as tf:
            tf.extractall(path=path)
