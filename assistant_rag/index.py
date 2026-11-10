"""步驟 2：分段、斷詞、建向量索引（可中斷，重跑會接續）。

  python -m assistant_rag.index build [--embedders bge-m3,embeddinggemma] [--no-activate]
  python -m assistant_rag.index status
  python -m assistant_rag.index probe "吃地瓜有什麼好處"      # 健全性檢查：BM25 與各向量索引前 5 名

語料讀 Data Root 的 raw/<來源>/articles.jsonl，略過 exclusions.json（status=approved 時）列的非飲食文章；相同語料＋分段＋斷詞版本會得到同一個 version，不重複寫入。
所有嵌入模型都完成後才標為 ready 並設為 active（--no-activate 則不切換）。
"""
import argparse
import hashlib
import json
import statistics
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from assistant_rag import store
from assistant_rag.bm25 import BM25
from assistant_rag.chunking import CHUNKER_VERSION, article_key, chunk_article, dedupe
from assistant_rag.config import data_dir
from assistant_rag.crawl import SOURCES, read_jsonl, source_dir
from assistant_rag.providers import EMBEDDERS, embedder as make_embedder
from assistant_rag.segment import default_tokenizer
from assistant_rag.sources import PARSER_VERSION, healthnews

BATCH = 32
EXCLUSIONS = Path(__file__).with_name("exclusions.json")


def load_corpus(root) -> list[dict]:
    records = []
    for source in SOURCES:
        records.extend(read_jsonl(source_dir(root, source) / "articles.jsonl"))
    return records


def normalize(record: dict) -> dict:
    """已抓取紀錄的標題清理（parser 修正前抓的資料也一致）。"""
    if record.get("source") == healthnews.SOURCE:
        record = {**record, "title": healthnews.clean_title(record["title"])}
    return record


def load_exclusions(path: Path = EXCLUSIONS) -> tuple[frozenset, str]:
    """排除名單（非飲食文章）。只有 status=approved 才生效；回傳（文章 key, 狀態）。"""
    if not path.exists():
        return frozenset(), "missing"
    data = json.loads(path.read_text(encoding="utf-8"))
    status = data.get("status", "pending_review")
    keys = frozenset(a["article_key"] for a in data.get("articles", []))
    return (keys if status == "approved" else frozenset()), status


def prepare(records: list[dict], tokenizer, excluded: frozenset = frozenset()) -> tuple[dict, list[dict], list]:
    records = [normalize(r) for r in records]
    excluded_count = sum(article_key(r) in excluded for r in records)
    records = [r for r in records if article_key(r) not in excluded]
    kept, duplicates = dedupe(records)
    by_keeper: dict[str, list[str]] = {}
    for dup, keeper in duplicates.items():
        by_keeper.setdefault(keeper, []).append(dup)
    articles, chunks = [], []
    for r in kept:
        key = article_key(r)
        articles.append({**r, "article_key": key, "duplicates": sorted(by_keeper.get(key, []))})
        for c in chunk_article(r):
            c.tokens = tokenizer.tokens(c.embed_text)
            chunks.append(c)
    lengths = [c.char_count for c in chunks]
    per_source = {s: {"articles": sum(a["source"] == s for a in articles),
                      "chunks": sum(c.article_key.startswith(s + ":") for c in chunks)} for s in SOURCES}
    corpus_hash = hashlib.sha256("\n".join(sorted(r["content_hash"] + r["url"] + r["title"] for r in records)).encode()).hexdigest()
    meta = {
        "parser_version": PARSER_VERSION, "chunker_version": CHUNKER_VERSION,
        "tokenizer_version": tokenizer.version, "corpus_hash": corpus_hash, "duplicate_count": len(duplicates),
        "excluded_count": excluded_count,
        "stats": {"per_source": per_source, "chunk_chars_avg": round(statistics.mean(lengths), 1),
                  "chunk_chars_median": statistics.median(lengths), "chunk_chars_max": max(lengths),
                  "tokens_avg": round(statistics.mean(len(c.tokens) for c in chunks), 1),
                  "dictionary_words": len(tokenizer.words), "excluded_articles": excluded_count},
    }
    return meta, articles, chunks


def version_name(meta: dict) -> str:
    digest = hashlib.sha256(json.dumps([meta["corpus_hash"], meta["parser_version"], meta["chunker_version"],
                                        meta["tokenizer_version"]]).encode()).hexdigest()[:10]
    return f"v-{digest}"


def embed_all(conn, version: str, name: str, *, log=print, factory=make_embedder) -> int:
    emb = factory(name)
    digest = emb.fingerprint()
    run = conn.execute("SELECT digest, status FROM embedding_runs WHERE version=%s AND embedder=%s",
                       (version, name)).fetchone()
    if run and run[0] != digest:
        raise RuntimeError(f"{name} 模型版本已變更（{run[0][:12]} → {digest[:12]}），請建立新的索引版本")
    if not run:
        conn.execute("INSERT INTO embedding_runs (version, embedder, model, digest, dimensions, template, status)"
                     " VALUES (%s,%s,%s,%s,%s,%s,'building')",
                     (version, name, emb.model, digest, emb.dimensions, emb.template))
        conn.commit()
    todo = store.missing_embeddings(conn, version, name)
    log(f"[{name}] 待嵌入 {len(todo)} 片")
    started = time.monotonic()
    for i in range(0, len(todo), BATCH):
        batch = todo[i:i + BATCH]
        vectors = emb.documents([t for _, t, _ in batch], [title for _, _, title in batch])
        store.save_embeddings(conn, version, name, [(cid, v) for (cid, _, _), v in zip(batch, vectors)])
        if (i // BATCH) % 20 == 0:
            log(f"[{name}] {min(i + BATCH, len(todo))}/{len(todo)}")
    if emb.fingerprint() != digest:
        raise RuntimeError(f"{name} 模型在建索引期間變更，請重建")
    seconds = time.monotonic() - started
    count = conn.execute(f"SELECT count(*) FROM {store.embed_table(name)} WHERE version=%s", (version,)).fetchone()[0]
    conn.execute("UPDATE embedding_runs SET status='ready', chunk_count=%s, finished_at=now(), seconds=seconds+%s"
                 " WHERE version=%s AND embedder=%s", (count, seconds, version, name))
    conn.commit()
    log(f"[{name}] 完成 {count} 片，本次 {seconds:.0f} 秒")
    return count


def build(conn, root, embedders: list[str], *, activate: bool = True, log=print, factory=make_embedder) -> str:
    store.migrate(conn)
    excluded, status = load_exclusions()
    if status != "approved":
        log(f"排除名單狀態 {status}：本次不排除任何文章")
    meta, articles, chunks = prepare(load_corpus(root), default_tokenizer(), excluded)
    version = version_name(meta)
    exists = conn.execute("SELECT chunk_count FROM index_versions WHERE version=%s", (version,)).fetchone()
    if exists:
        log(f"版本 {version} 已有 {exists[0]} 片，接續嵌入")
    else:
        store.insert_corpus(conn, version, meta, articles, chunks)
        log(f"版本 {version}：{len(articles)} 篇（排除 {meta['excluded_count']}、去重 {meta['duplicate_count']}）、{len(chunks)} 片")
    for name in embedders:
        embed_all(conn, version, name, log=log, factory=factory)
    total = conn.execute("SELECT chunk_count FROM index_versions WHERE version=%s", (version,)).fetchone()[0]
    ready = {r[0] for r in conn.execute("SELECT embedder FROM embedding_runs WHERE version=%s AND status='ready'"
                                        " AND chunk_count=%s", (version, total))}
    if ready >= set(EMBEDDERS):
        with conn.transaction():
            conn.execute("UPDATE index_versions SET status='ready' WHERE version=%s", (version,))
            if activate:
                conn.execute("UPDATE index_versions SET active=false, status='retired' WHERE active AND version<>%s",
                             (version,))
                conn.execute("UPDATE index_versions SET active=true WHERE version=%s", (version,))
        log(f"版本 {version} ready{'，已設為 active' if activate else ''}")
    else:
        log(f"版本 {version} 尚缺：{', '.join(sorted(set(EMBEDDERS) - ready))}")
    return version


def status(conn) -> list[dict]:
    store.migrate(conn)
    out = []
    for v, st, active, ac, dc, cc, stats, created in conn.execute(
            "SELECT version, status, active, article_count, duplicate_count, chunk_count, stats, created_at"
            " FROM index_versions ORDER BY created_at"):
        runs = {e: {"model": m, "digest": d[:12], "status": s, "chunks": n, "seconds": round(sec)}
                for e, m, d, s, n, sec in conn.execute(
                    "SELECT embedder, model, digest, status, chunk_count, seconds FROM embedding_runs"
                    " WHERE version=%s ORDER BY embedder", (v,))}
        out.append({"version": v, "status": st, "active": active, "articles": ac, "duplicates": dc,
                    "chunks": cc, "created_at": created.isoformat(timespec="seconds"), "stats": stats,
                    "embeddings": runs})
    return out


def probe(conn, query: str, k: int = 5) -> dict:
    version = store.active_version(conn)
    if not version:
        raise RuntimeError("沒有 active 索引，請先 build")
    ids, docs = store.load_tokens(conn, version)
    tokenizer = default_tokenizer()
    results = {"version": version, "query_tokens": tokenizer.tokens(query),
               "bm25": BM25(ids, docs).search(tokenizer.tokens(query), k)}
    for name in EMBEDDERS:
        results[name] = store.vector_search(conn, version, name, make_embedder(name).query(query), k)
    details = store.chunk_details(conn, version, list({cid for key in EMBEDDERS | {"bm25": 0}
                                                         for cid, _ in results[key]}))
    for key in ["bm25", *EMBEDDERS]:
        results[key] = [{"chunk_id": cid, "score": round(score, 4), "title": details[cid]["title"],
                         "heading": " > ".join(details[cid]["heading_path"])} for cid, score in results[key]]
    return results


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="python -m assistant_rag.index", description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("command", choices=["build", "status", "probe"])
    parser.add_argument("query", nargs="?")
    parser.add_argument("--embedders", default=",".join(EMBEDDERS))
    parser.add_argument("--no-activate", action="store_true")
    parser.add_argument("--data-dir")
    args = parser.parse_args(argv)
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    root = data_dir(args.data_dir)
    with store.connect() as conn:
        if args.command == "build":
            names = [n.strip() for n in args.embedders.split(",") if n.strip()]
            stamp = datetime.now(timezone.utc).isoformat(timespec="seconds")
            print(f"開始 {stamp}")
            build(conn, root, names, activate=not args.no_activate)
        elif args.command == "status":
            print(json.dumps(status(conn), ensure_ascii=False, indent=2, default=str))
        else:
            if not args.query:
                parser.error("probe 需要查詢文字")
            print(json.dumps(probe(conn, args.query), ensure_ascii=False, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
