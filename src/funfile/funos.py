import os
import shutil


def makedirs(path):
    os.makedirs(path, exist_ok=True)


def delete(path):
    if os.path.exists(path):
        shutil.rmtree(path, ignore_errors=True)
