import os
import shutil

def makedirs(path):
    if not os.path.exists(path):
        os.makedirs(path)


 
def delete(path):
    if os.path.exists(path):
        shutil.rmtree(path)