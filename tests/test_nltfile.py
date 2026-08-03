import io
import tarfile
import tempfile
import unittest
import zipfile
from pathlib import Path
from threading import Thread
from unittest import mock

from nltfile import ConcurrentFile, get_size
from nltfile.compress import tarfile as nlt_tarfile
from nltfile.compress.allfile import extractall
from nltfile.file import copy


class NltFileTest(unittest.TestCase):
    def test_concurrent_file_flushes_and_stops(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "output.bin"
            chunks = [bytes([index]) * 3 for index in range(10)]
            with ConcurrentFile(target, mode="wb", capacity=2) as writer:
                threads = [
                    Thread(target=writer.write, args=(chunk, index * 3))
                    for index, chunk in enumerate(chunks)
                ]
                for thread in threads:
                    thread.start()
                for thread in threads:
                    thread.join()

            self.assertEqual(target.read_bytes(), b"".join(chunks))
            self.assertFalse(writer._thread.is_alive())

    def test_concurrent_file_propagates_write_errors(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "output.bin"
            with (
                mock.patch("nltfile.file.concurrent.get_logger"),
                self.assertRaises(TypeError),
                ConcurrentFile(target, mode="wb") as writer,
            ):
                writer.write("text")

    def test_extractall_recognizes_tar_gz_and_zip(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source.txt"
            source.write_text("content")

            tar_path = root / "archive.tar.gz"
            with tarfile.open(tar_path, "w:gz") as archive:
                archive.add(source, arcname=source.name)
            extractall(tar_path, root / "tar-output")
            self.assertEqual((root / "tar-output" / source.name).read_text(), "content")

            zip_path = root / "archive.zip"
            with zipfile.ZipFile(zip_path, "w") as archive:
                archive.write(source, source.name)
            extractall(zip_path, root / "zip-output")
            self.assertEqual((root / "zip-output" / source.name).read_text(), "content")

    def test_tar_rejects_paths_outside_destination(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            archive_path = root / "unsafe.tar"
            with tarfile.open(archive_path, "w") as archive:
                info = tarfile.TarInfo("../escaped.txt")
                content = b"escaped"
                info.size = len(content)
                archive.addfile(info, io.BytesIO(content))

            archive = nlt_tarfile.open(archive_path, "r:*")
            stream = archive._progress_stream
            with self.assertRaises(tarfile.ExtractError), archive:
                archive.extractall(root / "output")
            self.assertFalse((root / "escaped.txt").exists())
            self.assertTrue(stream.closed)

    def test_tar_accepts_file_objects(self):
        stream = io.BytesIO()
        with tarfile.open(fileobj=stream, mode="w") as archive:
            pass
        stream.seek(0)

        with nlt_tarfile.open(fileobj=stream, mode="r:*") as archive:
            self.assertEqual(archive.getmembers(), [])
        self.assertFalse(stream.closed)

    def test_public_file_helpers(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "source.txt"
            target = Path(directory) / "target.txt"
            source.write_text("content")

            self.assertEqual(get_size(source), len("content"))
            self.assertEqual(Path(copy(source, target)), target)


if __name__ == "__main__":
    unittest.main()
