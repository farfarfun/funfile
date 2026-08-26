import shutil


def copy(src, dst, follow_symlinks=True):
    return shutil.copy(src, dst, follow_symlinks=follow_symlinks)
