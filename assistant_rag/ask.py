"""命令列問答（本機試用）：python -m assistant_rag.ask "吃地瓜有什麼好處"，或 --interactive 連續對話（保留前文）。"""
import argparse
import sys

from assistant_rag import store
from assistant_rag.answer import Assistant, InvalidInput
from assistant_rag.retrieval import Retriever


def show(bot: Assistant, message: str, history: list[dict]) -> str:
    text = ""
    for event in bot.events(message, history):
        kind, data = event["event"], event["data"]
        if kind == "meta":
            print(f"［{data['type']}{'，已補上前文' if data['rewritten'] else ''}］")
        elif kind == "delta":
            text += data["text"]
            print(data["text"], end="", flush=True)
        elif kind == "references":
            refs = data
        elif kind == "done":
            print()
            for ref in locals().get("refs", []):
                if ref["n"] in data["citations"]:
                    who = "、".join(f"{k} {v}" for k, v in ref["reviewers"].items()) or ref["author"] or ""
                    print(f"  [{ref['n']}] {ref['title']}（{ref['source_name']}{'，' + who if who else ''}，"
                          f"{(ref['published_at'] or '')[:10]}）{ref['url']}")
            removed = f"，移除 {data['removed']}" if data["removed"] else ""
            score = f"，最高分 {data['top_score']:.3f}／門檻 {data['threshold']}" if data["top_score"] is not None else ""
            print(f"  （{data['method']}，{data['timings']['total']:.1f} 秒{score}{removed}）")
        elif kind == "error":
            print(f"\n客服暫時無法使用：{data['code']}")
    return text


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="python -m assistant_rag.ask", description=__doc__)
    parser.add_argument("message", nargs="?")
    parser.add_argument("--interactive", action="store_true")
    args = parser.parse_args(argv)
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    with store.connect() as conn:
        bot = Assistant(Retriever(conn))
        history: list[dict] = []
        queue = [args.message] if args.message else []
        while True:
            message = queue.pop(0) if queue else (input("\n你：").strip() if args.interactive else "")
            if not message:
                break
            try:
                reply = show(bot, message, history)
            except InvalidInput as exc:
                print(exc)
                continue
            history += [{"role": "user", "content": message}, {"role": "assistant", "content": reply}]
            if not args.interactive:
                break
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
