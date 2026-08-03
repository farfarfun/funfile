import os
import shutil


def makedirs(path):
    os.makedirs(path, exist_ok=True)


def delete(path):
    try:
        if os.path.isdir(path) and not os.path.islink(path):
            shutil.rmtree(path)
        else:
            os.remove(path)
    except FileNotFoundError:
        pass
