import shutil


def copy(src, dst, follow_symlinks=True):
    shutil.copy(src, dst, follow_symlinks=follow_symlinks)
