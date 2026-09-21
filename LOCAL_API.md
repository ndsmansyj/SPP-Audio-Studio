# 本机 API

API 仅在你手动启动时运行，只监听 `127.0.0.1`。它调用与 App 相同的本机模型和任务流程。

## 启动

```bash
"/Applications/SPP Audio Studio.app/Contents/Resources/runtime/python/bin/python3.11" \
  "/Applications/SPP Audio Studio.app/Contents/Resources/worker/local_api.py"
```

终端会显示地址和一次性 Token。保持终端开启，按 `Control-C` 停止。可用 `--port 8765` 指定端口，或用 `--token <自选密钥>` 固定 Token。请勿把 Token 放进公开仓库。

## 调用

请求头均需 `Authorization: Bearer <Token>`；POST 另需 `Content-Type: application/json`。路径均为本机绝对路径。

```bash
curl -H 'Authorization: Bearer <Token>' http://127.0.0.1:8765/v1/status

curl -X POST http://127.0.0.1:8765/v1/convert \
  -H 'Authorization: Bearer <Token>' -H 'Content-Type: application/json' \
  -d '{"input":"/绝对路径/歌曲.ncm","output_dir":"/绝对路径/输出"}'

curl -X POST http://127.0.0.1:8765/v1/separate \
  -H 'Authorization: Bearer <Token>' -H 'Content-Type: application/json' \
  -d '{"input":"/绝对路径/歌曲.mp3","keep":"both","format":"WAV"}'

curl -X POST http://127.0.0.1:8765/v1/clone \
  -H 'Authorization: Bearer <Token>' -H 'Content-Type: application/json' \
  -d '{"ref_audio":"/绝对路径/参考.wav","ref_text":"参考音原话","text":"要生成的话","output_dir":"/绝对路径/输出"}'
```

`ref_text` 可省略；需要已安装 Whisper 模型及运行环境。`keep` 可为 `instrumental`、`vocals` 或 `both`；`format` 可为 `MP3`、`WAV` 或 `FLAC`。成功返回 `{"ok":true,...}`；输入错误返回 400，任务失败返回 422。请求会等待任务完成，最长一小时。
