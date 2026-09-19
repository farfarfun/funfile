import hashlib
import io
import shutil
import tarfile
import tempfile
import unittest
import zipfile
from pathlib import Path
from threading import Thread
from unittest import mock

from funfile import (
    ConcurrentFile,
    bytes_to_human_readable,
    file_md5,
    file_sha1,
    file_sha256,
    file_sha512,
    file_tqdm_bar,
    get_size,
)
from funfile.compress import tarfile as fun_tarfile
from funfile.compress.allfile import extractall
from funfile.compress.tarfile import file_detar, file_entar
from funfile.file import copy
from funfile.funos import delete, makedirs
from funfile.pickle import dump, dumps, load, loads


class FunFileTest(unittest.TestCase):
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
                mock.patch("funfile.file.concurrent.get_logger"),
                self.assertRaises(TypeError),
                ConcurrentFile(target, mode="wb") as writer,
            ):
                writer.write("text")

    def test_concurrent_file_preserves_logging_errors_as_cause(self):
        with tempfile.TemporaryDirectory() as directory:
            logger = mock.Mock()
            logger.exception.side_effect = RuntimeError("logging failed")
            target = Path(directory) / "output.bin"

            with (
                mock.patch("funfile.file.concurrent.get_logger", return_value=logger),
                self.assertRaises(TypeError) as caught,
                ConcurrentFile(target, mode="wb") as writer,
            ):
                writer.write("text")

            self.assertIsInstance(caught.exception.__cause__, RuntimeError)

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

    def test_extractall_rejects_unknown_formats(self):
        with tempfile.TemporaryDirectory() as directory:
            archive = Path(directory) / "archive.7z"
            archive.touch()
            with self.assertRaises(shutil.ReadError):
                extractall(archive)

    def test_tar_rejects_paths_outside_destination(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            archive_path = root / "unsafe.tar"
            with tarfile.open(archive_path, "w") as archive:
                info = tarfile.TarInfo("../escaped.txt")
                content = b"escaped"
                info.size = len(content)
                archive.addfile(info, io.BytesIO(content))

            archive = fun_tarfile.open(archive_path, "r:*")
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

        with fun_tarfile.open(fileobj=stream, mode="r:*") as archive:
            self.assertEqual(archive.getmembers(), [])
        self.assertFalse(stream.closed)

    def test_public_file_helpers(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "source.txt"
            target = Path(directory) / "target.txt"
            source.write_text("content")

            self.assertEqual(get_size(source), len("content"))
            self.assertEqual(Path(copy(source, target)), target)

    def test_public_serialization_hash_and_filesystem_helpers(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            nested = root / "nested"
            makedirs(nested)
            makedirs(nested)

            source = nested / "source.bin"
            source.write_bytes(b"abc")
            for algorithm, function in (
                ("md5", file_md5),
                ("sha1", file_sha1),
                ("sha256", file_sha256),
                ("sha512", file_sha512),
            ):
                self.assertEqual(
                    function(source), hashlib.new(algorithm, b"abc").hexdigest()
                )

            self.assertEqual(bytes_to_human_readable(0), "0B")
            self.assertEqual(bytes_to_human_readable(1024), "1.00KB")
            self.assertEqual(get_size(nested, recursive=True), 3)

            progress = file_tqdm_bar(source, disable=True)
            self.addCleanup(progress.close)
            self.assertEqual(progress.total, 3)

            value = {"key": [1, 2, 3]}
            pickle_path = root / "value.pkl"
            dump(value, pickle_path)
            self.assertEqual(load(pickle_path), value)
            self.assertEqual(loads(dumps(value)), value)

            delete(source)
            delete(source)
            delete(nested)
            self.assertFalse(nested.exists())
            with self.assertRaises(FileNotFoundError):
                get_size(root / "missing")

    def test_tar_shortcuts_round_trip(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source"
            source.mkdir()
            (source / "data.txt").write_text("content")

            archive = file_entar(source)
            output = root / "output"
            self.assertEqual(file_detar(archive, output), str(output))
            self.assertEqual((output / source.name / "data.txt").read_text(), "content")


if __name__ == "__main__":
    unittest.main()
