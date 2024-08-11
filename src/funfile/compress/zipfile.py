import zipfile


class ZipFile(zipfile.ZipFile):
    def __init__(self, file, mode="r", *args, **kwargs):
        super(ZipFile, self).__init__(file=file, mode=mode, *args, **kwargs)
