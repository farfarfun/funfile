## 安装

```python
pip install funfile
```

## 使用

### tafile 带进度条,用法和 tarfile 用法一致

```python
from funfile import tarfile
# 压缩
with tarfile.open("results.tar", "w|xz") as tar:
    tar.add("a.txt")

# 解压
with tarfile.open("results.tar", "r|xz") as tar:
    tar.extractall("local")
```

### 异步写入，适合多线程使用

```python
from funfile import ConcurrentFile
with ConcurrentFile("a.txt", mode='w') as fw:
    fw.write("hello,funfile.")
```
