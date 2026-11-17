"""逐段回放文本会议：python meeting.py 03A"""
import argparse
import json
import os
from copy import deepcopy
from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv
from zai import ZhipuAiClient

ROOT = Path(__file__).resolve().parent
MODEL = "glm-5.3-flash"
STATES = {"决定": ["有效", "依据受质疑", "暂缓"], "问题": ["待确认", "已关闭"], "待办": ["有效", "待确认", "已完成"]}
PROMPT = """你是会议记录员。根据当段发言，通过工具维护决定、问题和待办。完成所有写入后结束本轮。
仅采信已出现的原话。出现异议时，回读原决定原文核实，把原决定标为依据受质疑，并另建待确认问题。
只有明确拍板才能改变方案。明确改案时沿用原决定 ID 修订、关闭对应问题、沿用原任务 ID 改写准备工作。
暂缓时标记原决定暂缓，保留待确认问题，将依赖该方案的原准备任务标为待确认，另建明确指派的核实任务。
修改既有待办前先查询工具。负责人和期限必须有明确依据，区分计划上线时间、再议时间与任务期限。
记录内容写清实际决定、问题或动作；承接上一句的答复要补全指代。每次写入自动引用当前段。无需重复写入。"""


def dump(value):
    return json.dumps(value, ensure_ascii=False)


def tool(name, description, fields, required):
    return {"type": "function", "function": {"name": name, "description": description,
            "parameters": {"type": "object", "properties": fields, "required": required,
                           "additionalProperties": False}}}


STRING = {"type": "string"}
TOOLS = [
    tool("read_segment", "按段号回读已发生的原话", {"segment": STRING}, ["segment"]),
    tool("list_tasks", "查询待办 ID、状态及修订历史", {}, []),
    tool("write_record", "新增或修订一条记录。新增时省略 id；修订时传原 ID。owner 和 due 仅用于待办，未知留空。",
         {"kind": {"type": "string", "enum": list(STATES)}, "id": STRING, "text": STRING,
          "status": {"type": "string", "enum": sorted(set(sum(STATES.values(), [])))},
          "owner": STRING, "due": STRING}, ["kind", "text", "status"]),
]


def dispatch(name, args, seen, records, current):
    if not isinstance(args, dict) or any(not isinstance(v, str) for v in args.values()):
        raise ValueError("参数必须为字符串字段组成的对象")
    if name == "read_segment" and set(args) == {"segment"}:
        if args["segment"] not in seen:
            raise ValueError("段号不在已读范围内")
        return seen[args["segment"]]
    if name == "list_tasks" and not args:
        return {i: r for i, r in records.items() if r["kind"] == "待办"}
    if name != "write_record" or args.keys() - {"kind", "id", "text", "status", "owner", "due"}:
        raise ValueError("未知工具或参数")
    kind, status = args.get("kind"), args.get("status")
    if status not in STATES.get(kind, []) or not args.get("text", "").strip():
        raise ValueError("检查记录类型、状态和正文")
    rid = args.get("id")
    if rid:
        if rid not in records or records[rid]["kind"] != kind:
            raise ValueError("修订必须传入同类型的已有 ID")
    else:
        prefix = {"决定": "D", "问题": "Q", "待办": "T"}[kind]
        rid = prefix + str(1 + sum(r["kind"] == kind for r in records.values()))
        records[rid] = {"kind": kind, "versions": []}
    versions = records[rid]["versions"]
    entry = {"source": current, "text": args["text"], "status": status}
    if kind == "待办":
        previous = versions[-1] if versions else {}
        for field in ("owner", "due"):
            entry[field] = args.get(field, previous.get(field, ""))
    if not versions or versions[-1] != entry:
        versions.append(entry)
    return {"id": rid, **entry}


def process(client, seen, records, current, log):
    draft = deepcopy(records)
    history = list(seen)[:-1][-1:]
    context = {"当前段": {current: seen[current]}, "最近原文": {i: seen[i] for i in history},
               "已有纪要": records}
    messages = [{"role": "system", "content": PROMPT}, {"role": "user", "content": dump(context)}]
    log({"segment": current, "context": context, "model": MODEL})
    for _ in range(16):
        reply = client.chat.completions.create(model=MODEL, messages=messages, tools=TOOLS,
                                               temperature=0.1, stream=False).choices[0]
        msg = reply.message
        if not msg.tool_calls:
            if reply.finish_reason != "stop":
                raise RuntimeError(f"模型提前结束（{reply.finish_reason}）")
            if not draft:
                raise RuntimeError("模型未写入首份纪要")
            return draft
        messages.append(msg.model_dump(exclude_none=True))
        for call in msg.tool_calls:
            fn = call.function
            try:
                result = dispatch(fn.name, json.loads(fn.arguments), seen, draft, current)
            except ValueError as exc:
                result = {"error": str(exc)}
            log({"segment": current, "call_id": call.id, "tool": fn.name,
                 "arguments": fn.arguments, "result": result})
            messages.append({"role": "tool", "tool_call_id": call.id, "content": dump(result)})
    raise RuntimeError("超过每段工具调用轮数上限")


def replay(client, ending, log):
    seen, records = {}, {}
    for filename in ("common", ending):
        with (ROOT / "samples" / (filename + ".jsonl")).open() as lines:
            for line in lines:
                segment = json.loads(line) # 把这一行从 JSON 字符串解析成 Python 字典，类似 JSON.parse(line)
                current = segment["id"] # 如果没有 "id" 这个键，这里会直接抛出 KeyError
                if current in seen:
                    raise ValueError("段号重复")
                seen[current] = segment["text"]
                print(f"\n[{current}] {seen[current]}", flush=True)
                records = process(client, seen, records, current, log)
                log({"segment": current, "records": records})
                # 打印截至当前的完整纪要 TODO未读
                for rid, record in records.items():
                    versions = record["versions"]
                    v = versions[-1]
                    sources = " → ".join(dict.fromkeys(x["source"] for x in versions))
                    assignment = (f"（负责人 {v['owner'] or '未指定'}，期限 {v['due'] or '未指定'}）"
                                  if record["kind"] == "待办" else "")
                    print(f"{rid} {record['kind']} · {v['status']}  {v['text']}{assignment} [{sources}]", flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__) # 运行 --help 时会显示文件顶部三引号中的说明文字
    # 声明入参
    parser.add_argument("ending", choices=["03A", "03B"])
    # 读取并检查命令行参数
    args = parser.parse_args()
    
    # 读取 .env 文件，把里面的配置加载到环境变量中
    load_dotenv(ROOT / ".env", override=True)
    key = os.getenv("ZHIPUAI_API_KEY")
    if not key:
        parser.exit(1, "请在 .env 配置 ZHIPUAI_API_KEY\n")
    client = ZhipuAiClient(api_key=key, timeout=90, max_retries=1)
    directory = ROOT / "traces"
    # 创建目录，如果这个目录已经存在，就继续执行
    directory.mkdir(exist_ok=True)
    # 格式化字符串命名：获取当前时间，并按冒号后面的规则格式化
    path = directory / f"{datetime.now():%Y%m%d-%H%M%S-%f}-{args.ending}.jsonl"
    print(f"执行记录：{path}", flush=True)
    """
    上下文管理机制：with 用于在进入代码块时准备资源，离开时自动清理资源，也能管理数据库连接、锁等资源；as 负责绑定对象；正常执行结束、发生异常，或者执行 return，都会触发文件关闭
    1. path.open("x") 返回文件对象。
    2. with 调用它的 __enter__() 方法，把返回值绑定给 trace。
    3. 退出代码块时，`with` 调用它的 __exit__() 方法，完成文件关闭。
    """
    with path.open("x", buffering=1) as trace:
        def log(event):
            print(dump(event), file=trace)
        try:
            replay(client, args.ending, log)
        except Exception as exc: # 捕获 Exception 及其子类异常
            message = str(exc).replace(key, "[redacted]") # 若其中出现完整的 API 密钥，它替换为 [redacted]
            log({"error": message, "model": MODEL})
            parser.exit(1, message + "\n")

if __name__ == "__main__":
    main()
