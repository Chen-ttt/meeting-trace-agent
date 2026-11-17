# 议迹 · 文本会议回放

使用 Python 和智谱官方 zai-sdk 调用 glm-5.3-flash。项目根目录的 `.env` 中需配置 `ZHIPUAI_API_KEY`。

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python meeting.py 03A
.venv/bin/python meeting.py 03B
```

每次运行从空纪要开始，依次读取 `samples/common.jsonl` 和所选结尾。终端先显示带段号的原话，再显示本轮纪要。每条记录有稳定 ID，方括号列出历次修订的来源段号。

每轮请求包含当前片段、上一段原文和已有纪要。工具只可回读已发生的片段。工具写入先作用于本轮草稿，模型正常结束后提交。终端首行给出此次运行的 JSONL 日志路径，其中保存输入上下文、工具名、调用 ID、参数、结果及每段提交的状态。可按段号核对原话，按记录 ID 查看历次正文和状态。

离线工具测试可用 `.venv/bin/python -m unittest -v` 运行。两个结尾的语义验收以实际模型回放和 MVP 文档中的验收表为准。
