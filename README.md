# funfile

一个实用的 Python 文件操作工具库，提供带进度条的压缩/解压、并发写入、pickle 序列化等功能的增强封装。

## 特性

- 带进度条的 tar/zip 压缩与解压，用法与标准库一致
- 线程安全的并发文件写入，支持断点续写
- pickle 序列化/反序列化的便捷封装
- 常用文件系统操作（创建目录、删除、复制）

## 安装

```bash
pip install funfile
```

## 依赖

- Python >= 3.7
- [funutil](https://pypi.org/project/funutil/) >= 1.0.15
- [tqdm](https://pypi.org/project/tqdm/) >= 4.66.5

## 使用

### tar 压缩/解压（带进度条）

用法与标准库 `tarfile` 一致，自动显示进度条：

```python
from funfile import tarfile

# 压缩
with tarfile.open("results.tar", "w|xz") as tar:
    tar.add("a.txt")

# 解压
with tarfile.open("results.tar", "r|xz") as tar:
    tar.extractall("local")
```

也可以使用快捷函数：

```python
from funfile.compress.tarfile import file_entar, file_detar

# 一键压缩
file_entar("mydir", "mydir.tar")

# 一键解压
file_detar("mydir.tar", "output")
```

### zip 解压

```python
from funfile import zipfile

with zipfile.ZipFile("archive.zip") as zf:
    zf.extractall("output")
```

### 通用解压

根据文件后缀自动选择解压方式，支持 `.zip`、`.tar`、`.tar.gz`、`.tgz`、`.tar.bz2`、`.tar.xz`、`.txz`：

```python
from funfile.compress.allfile import extractall

extractall("archive.tar.gz", "output")
```

### 并发文件写入

线程安全的异步写入，适用于多线程场景。支持指定偏移量写入，并在写入过程中自动记录进度，异常中断后可断点续写：

```python
from funfile import ConcurrentFile

with ConcurrentFile("output.txt", mode="w") as fw:
    fw.write("hello, funfile.")
    fw.write("another line.")
```

支持指定偏移量的随机写入：

```python
with ConcurrentFile("output.bin", mode="wb") as fw:
    fw.write(b"chunk1", offset=0)
    fw.write(b"chunk2", offset=1024)
```

### pickle 序列化

便捷的 pickle 读写封装：

```python
from funfile.pickle import dump, load, dumps, loads

# 写入文件
dump({"key": "value"}, "data.pkl")

# 从文件读取
data = load("data.pkl")

# 序列化为 bytes
raw = dumps({"key": "value"})

# 从 bytes 反序列化
obj = loads(raw)
```

### 文件系统工具

```python
from funfile.funos import makedirs, delete

# 递归创建目录（已存在不报错）
makedirs("path/to/dir")

# 递归删除目录
delete("path/to/dir")
```

```python
from funfile.file import copy

# 复制文件
copy("src.txt", "dst.txt")
```

### 获取文件/目录大小

```python
from funfile import get_size

# 获取文件大小（字节）
size = get_size("large_file.bin")

# 递归获取目录大小
size = get_size("mydir", recursive=True)
```

## 项目结构

```
src/funfile/
├── __init__.py            # 顶层导出
├── funos.py               # 文件系统工具（makedirs, delete）
├── file/
│   ├── core.py            # 文件复制
│   └── concurrent.py      # 并发文件写入
├── compress/
│   ├── utils.py           # 进度条、文件大小计算
│   ├── tarfile.py         # 带进度条的 tar 操作
│   ├── zipfile.py         # zip 操作封装
│   └── allfile.py         # 通用解压
└── pickle/
    └── core.py            # pickle 序列化封装
```

## 许可证

[Apache License 2.0](LICENSE)
