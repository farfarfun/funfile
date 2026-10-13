import hashlib
import importlib.util
import io
import random
import shutil
import sys
import tarfile
import tempfile
import unittest
import warnings
import zipfile
from pathlib import Path
from threading import Lock, Thread
from unittest import mock

from tqdm import tqdm

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
from funfile.compress import zipfile as fun_zipfile
from funfile.compress.allfile import extractall
from funfile.compress.tarfile import file_detar, file_entar
from funfile.file import copy
from funfile.funos import delete, makedirs
from funfile.pickle import dump, dumps, load, loads

# zstd 可用性：Python 3.14 起标准库自带（PEP 784），更早版本需要第三方 zstandard。
_HAS_ZSTD = importlib.util.find_spec("zstandard") is not None or sys.version_info >= (
    3,
    14,
)


class _NonSeekableReader(io.RawIOBase):
    """只支持顺序读取的流，等价于管道 / socket / HTTP 响应体。

    不实现 `seek`/`tell`，由 `RawIOBase` 默认抛 `io.UnsupportedOperation`。
    """

    def __init__(self, path: Path) -> None:
        self._file = open(path, "rb")  # noqa: SIM115 - 由 close() 关闭

    def readable(self) -> bool:
        return True

    def readinto(self, buffer) -> int:
        data = self._file.read(len(buffer))
        buffer[: len(data)] = data
        return len(data)

    def close(self) -> None:
        self._file.close()
        super().close()


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

    def test_concurrent_file_close_is_idempotent_and_rejects_later_writes(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "output.bin"
            writer = ConcurrentFile(target, mode="wb")
            writer.write(b"payload")
            writer.close()
            writer.close()
            self.assertEqual(target.read_bytes(), b"payload")
            with self.assertRaises(ValueError):
                writer.write(b"too late")

    def test_concurrent_file_survives_close_racing_pending_writes(self):
        # 入队阻塞发生在状态锁之外，所以 close() 必须等在途的写入落队，
        # 否则结束标记会插到数据前面，已确认的写入被悄悄丢掉。
        for _ in range(20):
            with tempfile.TemporaryDirectory() as directory:
                target = Path(directory) / "output.bin"
                writer = ConcurrentFile(target, mode="wb", capacity=1)
                accepted = []
                lock = Lock()

                def write_one(index, writer=writer, accepted=accepted, lock=lock):
                    try:
                        writer.write(bytes([index]) * 8, index * 8)
                    except ValueError:
                        return  # 关闭之后再写，报错是预期行为
                    with lock:
                        accepted.append(index)

                threads = [Thread(target=write_one, args=(i,)) for i in range(16)]
                for thread in threads:
                    thread.start()
                closer = Thread(target=writer.close)
                closer.start()
                for thread in threads:
                    thread.join()
                closer.join(timeout=30)

                self.assertFalse(closer.is_alive(), "close() 不应卡死")
                data = target.read_bytes()
                for index in accepted:
                    chunk = data[index * 8 : index * 8 + 8]
                    self.assertEqual(chunk, bytes([index]) * 8)

    def test_concurrent_file_warns_on_deprecated_timeout(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "output.bin"
            with self.assertWarns(DeprecationWarning) as caught:
                ConcurrentFile(target, mode="wb", timeout=5).close()
            self.assertIn("timeout", str(caught.warning))

            # 不传就不该告警。
            with warnings.catch_warnings():
                warnings.simplefilter("error", DeprecationWarning)
                ConcurrentFile(target, mode="wb").close()

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

    def test_tar_stream_mode_round_trip(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source.txt"
            source.write_text("content")

            archive_path = root / "stream.tar.xz"
            with fun_tarfile.open(archive_path, "w|xz") as archive:
                archive.add(source, arcname=source.name)

            output = root / "output"
            with fun_tarfile.open(archive_path, "r|xz") as archive:
                archive.extractall(output)
            self.assertEqual((output / source.name).read_text(), "content")

    def test_tar_stream_mode_rejects_paths_outside_destination(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            archive_path = root / "unsafe.tar.gz"
            with tarfile.open(archive_path, "w:gz") as archive:
                info = tarfile.TarInfo("../escaped.txt")
                content = b"escaped"
                info.size = len(content)
                archive.addfile(info, io.BytesIO(content))

            with (
                self.assertRaises(tarfile.ExtractError),
                fun_tarfile.open(archive_path, "r|gz") as archive,
            ):
                archive.extractall(root / "output")
            self.assertFalse((root / "escaped.txt").exists())

    def test_tar_extract_single_member_checks_destination(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            safe = root / "safe.txt"
            safe.write_text("safe")
            archive_path = root / "mixed.tar"
            with tarfile.open(archive_path, "w") as archive:
                archive.add(safe, arcname=safe.name)
                info = tarfile.TarInfo("../escaped.txt")
                content = b"escaped"
                info.size = len(content)
                archive.addfile(info, io.BytesIO(content))

            output = root / "output"
            with fun_tarfile.open(archive_path, "r:*") as archive:
                archive.extract("safe.txt", output)
                self.assertEqual((output / "safe.txt").read_text(), "safe")
                with self.assertRaises(tarfile.ExtractError):
                    archive.extract("../escaped.txt", output)
            self.assertFalse((root / "escaped.txt").exists())

    def test_tar_accepts_non_seekable_file_objects(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source.txt"
            source.write_text("content")
            archive_path = root / "stream.tar.gz"
            with tarfile.open(archive_path, "w:gz") as archive:
                archive.add(source, arcname=source.name)

            output = root / "output"
            with (
                _NonSeekableReader(archive_path) as stream,
                fun_tarfile.open(fileobj=stream, mode="r|gz") as archive,
            ):
                archive.extractall(output)
            self.assertEqual((output / source.name).read_text(), "content")

    @unittest.skipUnless(_HAS_ZSTD, "需要 zstandard")
    def test_tar_zstd_round_trip(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source.txt"
            source.write_text("content")

            archive_path = root / "archive.tar.zst"
            with fun_tarfile.open(archive_path, "w:zst") as archive:
                archive.add(source, arcname=source.name)
            self.assertTrue(archive_path.exists())

            output = root / "output"
            with fun_tarfile.open(archive_path, "r:zst") as archive:
                archive.extractall(output)
            self.assertEqual((output / source.name).read_text(), "content")

    @unittest.skipUnless(_HAS_ZSTD, "需要 zstandard")
    def test_tar_zstd_detected_by_wildcard_and_allfile(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source.txt"
            source.write_text("content")
            archive_path = root / "archive.tar.zst"
            with fun_tarfile.open(archive_path, "w:zst", level=3) as archive:
                archive.add(source, arcname=source.name)

            with fun_tarfile.open(archive_path, "r:*") as archive:
                self.assertEqual(archive.getnames(), [source.name])

            output = root / "output"
            extractall(archive_path, output)
            self.assertEqual((output / source.name).read_text(), "content")

    @unittest.skipUnless(_HAS_ZSTD, "需要 zstandard")
    def test_tar_zstd_rejects_paths_outside_destination(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            archive_path = root / "unsafe.tar.zst"
            with fun_tarfile.open(archive_path, "w:zst") as archive:
                info = tarfile.TarInfo("../escaped.txt")
                content = b"escaped"
                info.size = len(content)
                archive.addfile(info, io.BytesIO(content))

            with (
                self.assertRaises(tarfile.ExtractError),
                fun_tarfile.open(archive_path, "r:zst") as archive,
            ):
                archive.extractall(root / "output")
            self.assertFalse((root / "escaped.txt").exists())

    def test_tar_zstd_reports_missing_backend(self):
        # 两个后端都挡掉：第三方 zstandard 和 3.14+ 的标准库 compression.zstd。
        with (
            mock.patch.dict(sys.modules, {"zstandard": None, "compression.zstd": None}),
            self.assertRaises(tarfile.CompressionError) as caught,
        ):
            fun_tarfile.TarFile.zstopen("archive.tar.zst", "w")
        self.assertIn("funfile[zstd]", str(caught.exception))

    @unittest.skipUnless(_HAS_ZSTD, "需要 zstd 后端")
    def test_tar_zstd_backend_prefers_stdlib(self):
        # 3.14 起标准库自带 zstd，此时不应该再要求装第三方包。
        expected = "stdlib" if sys.version_info >= (3, 14) else "zstandard"
        self.assertEqual(fun_tarfile._zstd_backend()[0], expected)

    @unittest.skipUnless(sys.version_info >= (3, 14), "需要 Python 3.14+")
    def test_tar_zstd_works_without_third_party_module(self):
        with mock.patch.dict(sys.modules, {"zstandard": None}):
            self.assertEqual(fun_tarfile._zstd_backend()[0], "stdlib")
            with tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                source = root / "source.txt"
                source.write_text("content")
                archive_path = root / "archive.tar.zst"
                with fun_tarfile.open(archive_path, "w:zst") as archive:
                    archive.add(source, arcname=source.name)
                output = root / "output"
                with fun_tarfile.open(archive_path, "r:zst") as archive:
                    archive.extractall(output)
                self.assertEqual((output / source.name).read_text(), "content")

    def test_tar_zstd_rejects_invalid_mode(self):
        with self.assertRaises(ValueError):
            fun_tarfile.TarFile.zstopen("archive.tar.zst", "a")

    def test_tar_registers_zstd_without_patching_stdlib(self):
        self.assertIn("zst", fun_tarfile.TarFile.OPEN_METH)
        self.assertIsNot(fun_tarfile.TarFile.OPEN_METH, tarfile.TarFile.OPEN_METH)
        # 3.14 的标准库原生带 zst；更早版本不该因为导入 funfile 而多出这个键。
        if sys.version_info < (3, 14):
            self.assertNotIn("zst", tarfile.TarFile.OPEN_METH)

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
            self.assertTrue(archive.endswith(".tar.xz"))
            output = root / "output"
            self.assertEqual(file_detar(archive, output), str(output))
            self.assertEqual((output / source.name / "data.txt").read_text(), "content")

    def test_file_entar_honours_codec(self):
        codecs = ["tar", "gz", "bz2", "xz"] + (["zst"] if _HAS_ZSTD else [])
        for codec in codecs:
            with self.subTest(codec=codec), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                source = root / "source"
                source.mkdir()
                (source / "data.txt").write_text("content")

                archive = file_entar(source, codec=codec)
                self.assertTrue(archive.endswith(fun_tarfile.CODEC_SUFFIXES[codec]))

                output = root / "output"
                file_detar(archive, output)
                self.assertEqual(
                    (output / source.name / "data.txt").read_text(), "content"
                )

    def test_file_entar_rejects_unknown_codec(self):
        with self.assertRaises(ValueError) as caught:
            file_entar("source", codec="lz4")
        self.assertIn("unsupported codec", str(caught.exception))

    def test_tar_add_accumulates_progress_total(self):
        # 多次 add 时总量必须累加，否则进度会按首次调用的大小冲过 100%。
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            sizes = {"a.bin": 1024, "b.bin": 4096, "c.bin": 2048}
            for name, size in sizes.items():
                (root / name).write_bytes(b"x" * size)

            with fun_tarfile.open(root / "out.tar", "w:") as archive:
                for name in sizes:
                    archive.add(root / name, arcname=name)
                self.assertEqual(archive._progress.total, sum(sizes.values()))

    def test_tar_add_leaves_caller_progress_total_alone(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for name in ("a.bin", "b.bin"):
                (root / name).write_bytes(b"x" * 1024)
            bar = file_tqdm_bar("", total=4096, disable=True)
            self.addCleanup(bar.close)

            with fun_tarfile.open(root / "out.tar", "w:") as archive:
                archive.add(root / "a.bin", arcname="a.bin", progress=bar)
                archive.add(root / "b.bin", arcname="b.bin")
                self.assertEqual(bar.total, 4096)

    @staticmethod
    def _mixed_entropy_text() -> str:
        """生成有真实熵的可压缩文本，低压缩档与高压缩档才会分出体积差。"""
        generator = random.Random(7)
        words = ("handler", "request", "response", "config", "timeout", "retry")
        return "".join(
            f"{generator.choice(words)} {generator.random():.6f} id={index}\n"
            for index in range(20000)
        )

    def test_file_entar_accepts_codec_options(self):
        # 每种压缩类型的参数名都不同，这里只验证能透传到对应后端且归档可读。
        options = {
            "gz": {"compresslevel": 1},
            "bz2": {"compresslevel": 1},
            "xz": {"preset": 1},
        }
        if _HAS_ZSTD:
            options["zst"] = {"level": 3, "threads": 0}

        for codec, kwargs in options.items():
            with (
                self.subTest(codec=codec),
                tempfile.TemporaryDirectory() as directory,
            ):
                root = Path(directory)
                source = root / "source"
                source.mkdir()
                (source / "data.txt").write_text("content")

                archive = file_entar(source, codec=codec, **kwargs)
                output = root / "output"
                file_detar(archive, output)
                self.assertEqual(
                    (output / source.name / "data.txt").read_text(), "content"
                )

    def test_file_entar_codec_options_change_output_size(self):
        # bz2 的 compresslevel 是块大小而不是压缩力度，体积不单调，故不在此列。
        levels = {"gz": ("compresslevel", 1, 9), "xz": ("preset", 0, 6)}
        if _HAS_ZSTD:
            levels["zst"] = ("level", 1, 19)
        text = self._mixed_entropy_text()

        for codec, (option, fast, small) in levels.items():
            with (
                self.subTest(codec=codec),
                tempfile.TemporaryDirectory() as directory,
            ):
                root = Path(directory)
                source = root / "source"
                source.mkdir()
                (source / "data.txt").write_text(text)

                sizes = [
                    Path(
                        file_entar(
                            source,
                            root / f"{value}.archive",
                            codec=codec,
                            **{option: value},
                        )
                    )
                    .stat()
                    .st_size
                    for value in (fast, small)
                ]
                self.assertLess(sizes[1], sizes[0])

    def test_file_detar_reports_missing_zstd_backend(self):
        with tempfile.TemporaryDirectory() as directory:
            # 需要真实存在的文件，否则在读到压缩后端之前就先抛 FileNotFoundError。
            archive = Path(directory) / "archive.tar.zst"
            archive.write_bytes(b"not a real zstd frame")
            with (
                mock.patch.dict(
                    sys.modules, {"zstandard": None, "compression.zstd": None}
                ),
                self.assertRaises(tarfile.CompressionError) as caught,
            ):
                file_detar(archive, Path(directory) / "output")
            self.assertIn("funfile[zstd]", str(caught.exception))

    def test_extractall_recognizes_short_tar_extensions(self):
        aliases = {".tgz": "w:gz", ".taz": "w:gz", ".tbz2": "w:bz2", ".txz": "w:xz"}
        for suffix, mode in aliases.items():
            with (
                self.subTest(suffix=suffix),
                tempfile.TemporaryDirectory() as directory,
            ):
                root = Path(directory)
                source = root / "source.txt"
                source.write_text("content")
                archive_path = root / f"archive{suffix}"
                with tarfile.open(archive_path, mode) as archive:
                    archive.add(source, arcname=source.name)

                output = root / "output"
                extractall(archive_path, output)
                self.assertEqual((output / source.name).read_text(), "content")

    def _silent_bar(self, total: int) -> tqdm:
        """造一个会真正计数但不往终端输出的进度条。

        `disable=True` 的进度条 `update()` 是空操作、`n` 永远为 0，断言不到字节数，
        所以这里用 `file` 把输出引到内存里，而不是直接禁用。
        """
        bar = tqdm(total=total, file=io.StringIO(), disable=False)
        self.addCleanup(bar.close)
        return bar

    def test_zip_extractall_tracks_extracted_bytes(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            sizes = {"a.bin": 3000, "b.bin": 5000}
            for name, size in sizes.items():
                (root / name).write_bytes(b"x" * size)
            archive_path = root / "archive.zip"
            with fun_zipfile.ZipFile(archive_path, "w") as archive:
                for name in sizes:
                    archive.write(root / name, arcname=name)

            bar = self._silent_bar(sum(sizes.values()))
            with fun_zipfile.ZipFile(archive_path) as archive:
                archive.extractall(root / "output", progress=bar)
            self.assertEqual(bar.n, sum(sizes.values()))
            # 调用方自带的进度条由调用方关闭，解压结束时不能被顺手关掉。
            self.assertFalse(bar.disable)

    def test_zip_write_accumulates_progress_total(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            sizes = {"a.bin": 1024, "b.bin": 4096, "c.bin": 2048}
            for name, size in sizes.items():
                (root / name).write_bytes(b"x" * size)

            # 自建的进度条在非 TTY 下会被 tqdm 自动禁用、`n` 恒为 0，
            # 所以这里只断言总量；字节计数另用自带进度条验证。
            with fun_zipfile.ZipFile(root / "archive.zip", "w") as archive:
                for name in sizes:
                    archive.write(root / name, arcname=name)
                self.assertEqual(archive._progress.total, sum(sizes.values()))

            bar = self._silent_bar(sum(sizes.values()))
            with fun_zipfile.ZipFile(root / "counted.zip", "w") as archive:
                for name in sizes:
                    archive.write(root / name, arcname=name, progress=bar)
            self.assertEqual(bar.n, sum(sizes.values()))

    def test_zip_writestr_counts_encoded_bytes(self):
        with tempfile.TemporaryDirectory() as directory:
            text = "中文内容"
            with fun_zipfile.ZipFile(Path(directory) / "archive.zip", "w") as archive:
                archive.writestr("note.txt", text)
                # str 会被标准库编码成 UTF-8，总量按编码后的长度算才对得上。
                self.assertEqual(archive._progress.total, len(text.encode("utf-8")))
                self.assertGreater(len(text.encode("utf-8")), len(text))

    def test_zip_metadata_access_creates_no_progress_bar(self):
        with tempfile.TemporaryDirectory() as directory:
            archive_path = Path(directory) / "archive.zip"
            with fun_zipfile.ZipFile(archive_path, "w") as archive:
                archive.writestr("note.txt", "content")

            with fun_zipfile.ZipFile(archive_path) as archive:
                self.assertEqual(archive.namelist(), ["note.txt"])
                self.assertEqual(archive.read("note.txt"), b"content")
                self.assertIsNone(archive.testzip())
                # 只读元数据或整体读取不该凭空冒出一个进度条。
                self.assertIsNone(archive._progress)

    def test_zip_extract_single_member_round_trip(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            archive_path = root / "archive.zip"
            with fun_zipfile.ZipFile(archive_path, "w") as archive:
                archive.writestr("note.txt", "content")

            with fun_zipfile.ZipFile(archive_path) as archive:
                extracted = archive.extract("note.txt", root / "output")
            self.assertEqual(Path(extracted).read_text(), "content")

    def test_zip_subclasses_stdlib_without_changing_semantics(self):
        # 下游可能把 funfile 的 ZipFile 交给只认标准库类型的代码。
        self.assertTrue(issubclass(fun_zipfile.ZipFile, zipfile.ZipFile))


if __name__ == "__main__":
    unittest.main()
