# External Converter Adapter

SPP Audio Studio 的公开构建不内置特殊格式解密核心。用户可以在「模型与环境」选择一个自己已经准备好的本地兼容转换器，之后格式转换页仍保持原来的拖入即用流程。

## 调用约定

SPP 以本地进程方式调用：

```text
<converter> --in <source-file> --out <work-dir> --format <mp3|flac|original>
```

要求：

- 退出码 `0` 表示成功。
- 工作目录中必须产出一个 `.mp3` 或 `.flac` 文件。
- 源文件只读；SPP 只在临时工作目录和用户选择的输出目录写文件。
- SPP 不负责下载、安装、推荐或更新任何具体第三方转换器。

## 可选 metadata sidecar

转换器可以在工作目录写入 `spp-meta.json`，字段可包含 `title`、`artist`、`album`、`music_id`、`album_id` 和 `cover`。其中 `cover` 可以是工作目录中的相对路径或绝对路径。

SPP 会在最终输出阶段写入 MP3 / FLAC 标签与封面。

## 安全边界

External Converter Adapter 只负责本机进程编排和结果接收，不接触账号登录、Cookie、访问令牌、歌曲下载或云端转换。
