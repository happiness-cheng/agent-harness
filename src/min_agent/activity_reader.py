"""有界二进制/JSONL 读取；仅 rb，显式检查 zstd 帧尾和第二遍指纹。"""
import hashlib
import os
from pathlib import Path


class ReadFailure(Exception):
    """只携带固定错误码，禁止将底层异常正文写进报告。"""


def is_link(path):
    stat = path.lstat()
    return path.is_symlink() or bool(getattr(stat, "st_file_attributes", 0) & 0x400)


def signature(stat):
    return stat.st_size, stat.st_mtime_ns, stat.st_dev, stat.st_ino


def hash_readonly(path, limit):
    h = hashlib.sha256()
    total = 0
    with Path(path).open("rb") as f:
        before = signature(os.fstat(f.fileno()))
        while chunk := f.read(65536):
            total += len(chunk)
            if total > limit:
                raise ReadFailure("physical_size_limit")
            h.update(chunk)
        after = signature(os.fstat(f.fileno()))
    return h.hexdigest(), before == after == signature(Path(path).stat())


def lines(path, meta, *, max_line, max_bytes):
    """逐行输出 (零基行号, 解压字节偏移, 原始行或 None, 状态)。"""
    physical_hash, logical_hash = hashlib.sha256(), hashlib.sha256()
    meta.update(complete=False, stable=False, physical_byte_length=0, decompressed_content_length=0)
    with Path(path).open("rb") as f:
        initial = signature(os.fstat(f.fileno()))
        if initial[0] > max_bytes:
            raise ReadFailure("physical_size_limit")
        magic = f.read(4)
        f.seek(0)
        compressed = magic == b"\x28\xb5\x2f\xfd"
        if not compressed and Path(path).suffix.lower() in (".zst", ".zstd"):
            raise ReadFailure("unsupported_compression")
        meta["compression"] = "zstd" if compressed else "none"
        if compressed:
            try:
                import zstandard
            except ImportError:
                raise ReadFailure("missing_zstandard_dependency") from None
            decoder = zstandard.ZstdDecompressor(max_window_size=64*1024*1024).decompressobj()
        frames = 0

        def chunks():
            nonlocal decoder, frames
            # 小块输入限制一次解压的临时分配；不将整份历史放入内存。
            while data := f.read(256 if compressed else 65536):
                physical_hash.update(data)
                meta["physical_byte_length"] += len(data)
                if meta["physical_byte_length"] > max_bytes:
                    raise ReadFailure("physical_size_limit")
                if not compressed:
                    yield data
                    continue
                while data:
                    if decoder.eof:
                        decoder = zstandard.ZstdDecompressor(max_window_size=64*1024*1024).decompressobj()
                    try:
                        output = decoder.decompress(data)
                    except zstandard.ZstdError:
                        raise ReadFailure("invalid_zstd_stream") from None
                    data = decoder.unused_data if decoder.eof else b""
                    if output:
                        yield output
                    if decoder.eof:
                        frames += 1
                        if frames > 10000:
                            raise ReadFailure("frame_count_limit")
            if compressed and not decoder.eof:
                raise ReadFailure("unfinished_zstd_stream")

        pending = bytearray()
        offset, line_no, line_size, oversized = 0, 0, 0, False
        for chunk in chunks():
            logical_hash.update(chunk)
            meta["decompressed_content_length"] += len(chunk)
            if meta["decompressed_content_length"] > max_bytes:
                raise ReadFailure("logical_size_limit")
            start = 0
            while start < len(chunk):
                end = chunk.find(b"\n", start)
                stop = len(chunk) if end < 0 else end + 1
                part = chunk[start:stop]
                line_size += len(part)
                if line_size > max_line:
                    oversized = True
                    pending.clear()
                elif not oversized:
                    pending.extend(part)
                if end >= 0:
                    yield line_no, offset, None if oversized else bytes(pending), "line_size_limit" if oversized else "ok"
                    offset += line_size
                    line_no += 1
                    pending.clear()
                    line_size, oversized = 0, False
                start = stop
        if line_size:
            yield line_no, offset, None, "incomplete_line"
        meta["physical_sha256"] = physical_hash.hexdigest()
        meta["logical_sha256"] = logical_hash.hexdigest()
        first_stable = initial == signature(os.fstat(f.fileno())) == signature(Path(path).stat())
    second_hash, second_stable = hash_readonly(path, max_bytes)
    meta["stable"] = first_stable and second_stable and second_hash == meta["physical_sha256"]
    meta["complete"] = line_size == 0
