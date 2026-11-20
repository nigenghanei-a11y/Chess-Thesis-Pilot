"""Step 3 pilot: generate Condition A explanations (NO engine information) with local Ollama models.

Reads outputs/step1_pilot/<month>/pilot_for_llm.csv (10 positions, no labels) and, for every model and repeat,
writes one JSON line to outputs/step3_llm_pilot/<month>/<model>/generations.jsonl plus a run_manifest.json.
Afterwards it writes summary.csv (speed, truncation), variation.csv (run-to-run differences) and
review_sheet.csv (to count claims / errors by hand).

Run from the project folder, inside the .venv, with Ollama running (`ollama serve`) and the models pulled:
    python step3_llm_pilot.py --limit 2 --repeats 1        # smoke test
    python step3_llm_pilot.py --repeats 2                   # real pilot, all four models
Interrupted runs resume: finished (position, repeat) pairs are skipped if the settings are unchanged.
"""
import argparse, csv, hashlib, json, os, platform, re, statistics, subprocess, sys, time
import urllib.error, urllib.request
from datetime import datetime, timezone
from pathlib import Path

import config

# ---- step-local knobs (scientific settings live in config.py) ----
DEFAULT_MODELS = ["qwen2.5:7b", "llama3.1:8b", "deepseek-r1:8b", "phi4-mini"]   # check with `ollama list`
OLLAMA_URL = "http://localhost:11434"
REASONING_TAGS = ("deepseek-r1",)   # these models write a <think> block first and need a larger token budget

SYSTEM = "You are a strong chess player and coach."
USER_TEMPLATE = (
    "Chess position (FEN): {fen}\n{board}"
    "{side} to move.\nThe move played is {move} (standard algebraic notation).\n\n"
    "Explain this move in 3 to 5 sentences: what it does, what it changes in the position, "
    "and whether it is a good choice. Refer to specific pieces and squares."
)
THINK = re.compile(r"<think>(.*?)</think>", re.S)


def sha256_bytes(b): return hashlib.sha256(b).hexdigest()
def sha256_file(p): return sha256_bytes(Path(p).read_bytes())


def git(*a):
    try:
        return subprocess.run(["git", "-C", str(config.ROOT), *a], check=True, capture_output=True, text=True).stdout.strip()
    except (FileNotFoundError, subprocess.CalledProcessError):
        return None


def build_prompt(row, with_board):
    board = ""
    if with_board:
        import chess
        board = "Board (uppercase = White, lowercase = Black):\n" + str(chess.Board(row["fen"])) + "\n"
    side = row["side_to_move"].capitalize()
    return USER_TEMPLATE.format(fen=row["fen"], board=board, side=side, move=row["move_to_explain_san"])


def split_thinking(content, thinking_field=""):
    """Return (answer_text, thinking_text, think_unterminated)."""
    thinking = thinking_field or ""
    found = THINK.findall(content)
    text = THINK.sub("", content)
    thinking += "\n".join(found)
    if "</think>" in text:                      # opening tag missing
        head, text = text.split("</think>", 1)
        thinking += head
    unterminated = "<think>" in text
    if unterminated:                            # ran out of tokens while still thinking
        thinking += text.split("<think>", 1)[1]
        text = text.split("<think>", 1)[0]
    return text.strip(), thinking.strip(), unterminated


class OllamaBackend:
    def _call(self, path, payload=None):
        req = urllib.request.Request(OLLAMA_URL + path, data=None if payload is None else json.dumps(payload).encode(),
                                     headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=1800) as r:
                return json.loads(r.read())
        except urllib.error.URLError as e:
            raise SystemExit(f"Cannot reach Ollama at {OLLAMA_URL} ({e}). Start it with `ollama serve`, "
                             f"and pull the model first (`ollama pull <model>`).")

    def version(self): return self._call("/api/version").get("version")

    def digest(self, model):
        for m in self._call("/api/tags").get("models", []):
            if m.get("name") == model or m.get("model") == model:
                return m.get("digest")
        raise SystemExit(f"Model {model!r} is not installed. Run `ollama pull {model}` (see `ollama list`).")

    def chat(self, model, system, user, options):
        r = self._call("/api/chat", dict(model=model, stream=False, keep_alive="15m", options=options,
                                         messages=[{"role": "system", "content": system}, {"role": "user", "content": user}]))
        msg = r.get("message", {})
        return dict(content=msg.get("content", ""), thinking=msg.get("thinking", ""), done_reason=r.get("done_reason"),
                    eval_count=r.get("eval_count"), prompt_eval_count=r.get("prompt_eval_count"),
                    eval_s=(r.get("eval_duration") or 0) / 1e9, total_s=(r.get("total_duration") or 0) / 1e9)

    def warmup(self, model, options):
        self.chat(model, "Reply with one word.", "Say OK.", {**options, "num_predict": 1})


class MockBackend:
    """Offline stand-in used only to test this script (no model is run)."""
    def __init__(self): self.calls = 0
    def version(self): return "mock"
    def digest(self, model): return "mock-" + sha256_bytes(model.encode())[:12]
    def warmup(self, model, options): pass

    def chat(self, model, system, user, options):
        self.calls += 1
        move = re.search(r"move played is (\S+)", user).group(1)
        h = int(sha256_bytes((model + user).encode())[:6], 16)
        body = f"The move {move} develops the position and improves piece activity. It is a reasonable choice. [{h % 7}]"
        if "phi" in model and self.calls % 2 == 0:
            body += " Variant."                                  # simulate run-to-run variation
        done = "stop"
        if "deepseek" in model:
            body = f"<think>Let me look at {move} carefully.</think>" + body
            if h % 5 == 0:
                body, done = "<think>I am still thinking about", "length"   # simulate truncation inside the think block
        elif "llama" in model and h % 4 == 0:
            body, done = body[:40], "length"
        return dict(content=body, thinking="", done_reason=done, eval_count=60, prompt_eval_count=120,
                    eval_s=6.0 + (h % 3), total_s=7.0 + (h % 3))


def read_done(path, settings_sha):
    done = set()
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                d = json.loads(line)
                if d["settings_sha256"] != settings_sha:
                    raise SystemExit(f"{path} was made with different settings/prompt. Move that folder aside before re-running.")
                done.add((d["pilot_id"], d["repeat"]))
    return done


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--models", default=",".join(DEFAULT_MODELS), help="comma-separated Ollama tags")
    ap.add_argument("--repeats", type=int, default=config.LLM_REPEATS)
    ap.add_argument("--limit", type=int, default=None, help="only the first N pilot positions (smoke test)")
    ap.add_argument("--threads", type=int, default=max(1, (os.cpu_count() or 2) // 2), help="CPU threads, recorded in the manifest")
    ap.add_argument("--reasoning-max-tokens", type=int, default=3000)
    ap.add_argument("--with-board", action="store_true", help="add an ASCII board to the prompt (design decision: off by default)")
    ap.add_argument("--mock", action="store_true", help="test the script without Ollama")
    args = ap.parse_args()

    month = config.MONTH
    pilot_csv = config.OUT_DIR / "step1_pilot" / month / "pilot_for_llm.csv"
    rows = list(csv.DictReader(open(pilot_csv, encoding="utf-8", newline="")))
    if args.limit: rows = rows[: args.limit]
    assert rows and set(rows[0]) == {"pilot_id", "fen", "side_to_move", "move_to_explain_san"}, "unexpected pilot_for_llm.csv columns"
    out_root = config.OUT_DIR / "step3_llm_pilot" / (month + ("_MOCK" if args.mock else ""))
    backend = MockBackend() if args.mock else OllamaBackend()
    prompts = {r["pilot_id"]: build_prompt(r, args.with_board) for r in rows}
    prompt_sha = sha256_bytes(json.dumps([SYSTEM, USER_TEMPLATE, args.with_board]).encode())
    models = [m.strip() for m in args.models.split(",") if m.strip()]

    for model in models:
        reasoning = any(t in model.lower() for t in REASONING_TAGS)
        options = dict(temperature=config.LLM_TEMPERATURE, seed=config.SEED_LLM, num_gpu=0, num_thread=args.threads,
                       num_ctx=max(config.LLM_NUM_CTX, 4096) if reasoning else config.LLM_NUM_CTX,
                       num_predict=args.reasoning_max_tokens if reasoning else config.LLM_MAX_TOKENS)
        settings = dict(model=model, condition="A", options=options, prompt_sha256=prompt_sha, with_board=args.with_board)
        settings_sha = sha256_bytes(json.dumps(settings, sort_keys=True).encode())
        mdir = out_root / re.sub(r"[^A-Za-z0-9._-]", "_", model); mdir.mkdir(parents=True, exist_ok=True)
        gen_path = mdir / "generations.jsonl"
        done = read_done(gen_path, settings_sha)
        digest = backend.digest(model)
        print(f"\n== {model} (digest {digest[:12]}, {len(done)} already done) ==", flush=True)
        backend.warmup(model, options)          # model load time must not count as generation time
        with gen_path.open("a", encoding="utf-8", newline="\n") as f:
            for repeat in range(1, args.repeats + 1):
                for r in rows:
                    if (r["pilot_id"], repeat) in done: continue
                    t0 = time.time()
                    res = backend.chat(model, SYSTEM, prompts[r["pilot_id"]], options)
                    text, thinking, unterminated = split_thinking(res["content"], res["thinking"])
                    tps = res["eval_count"] / res["eval_s"] if res["eval_s"] else None
                    rec = dict(pilot_id=r["pilot_id"], repeat=repeat, model=model, model_digest=digest, condition="A",
                               text=text, thinking=thinking, think_unterminated=unterminated, raw_content=res["content"],
                               done_reason=res["done_reason"], truncated=(res["done_reason"] == "length"),
                               eval_count=res["eval_count"], prompt_eval_count=res["prompt_eval_count"],
                               seconds=round(res["total_s"], 2), tokens_per_s=None if tps is None else round(tps, 2),
                               settings_sha256=settings_sha, prompt=prompts[r["pilot_id"]],
                               timestamp_utc=datetime.now(timezone.utc).isoformat(timespec="seconds"))
                    f.write(json.dumps(rec, ensure_ascii=False) + "\n"); f.flush()
                    print(f"  {r['pilot_id']} r{repeat}: {rec['seconds']:>6.1f}s  {rec['eval_count']} tok  {rec['done_reason']}", flush=True)
        manifest = dict(step="step3_llm_pilot", condition="A", model=model, model_digest=digest, ollama_version=backend.version(),
                        settings=settings, settings_sha256=settings_sha, system_prompt=SYSTEM, user_template=USER_TEMPLATE,
                        prompt_sha256=prompt_sha, input_csv_sha256=sha256_file(pilot_csv), n_positions=len(rows), repeats=args.repeats,
                        config=config.config_snapshot(), git_commit=git("rev-parse", "HEAD"),
                        git_uncommitted_changes=bool(git("status", "--porcelain")), script_sha256=sha256_file(__file__),
                        python=sys.version.split()[0], platform=platform.platform(), processor=platform.processor(),
                        cpu_count=os.cpu_count(), mock=args.mock,
                        timestamp_utc=datetime.now(timezone.utc).isoformat(timespec="seconds"))
        (mdir / "run_manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")

    # ---- cross-model tables ----
    recs = []
    for model in models:
        p = out_root / re.sub(r"[^A-Za-z0-9._-]", "_", model) / "generations.jsonl"
        recs += [json.loads(l) for l in p.read_text(encoding="utf-8").splitlines() if l.strip()]
    summary, variation = [], []
    for model in models:
        m = [r for r in recs if r["model"] == model]
        by_pos = {}
        for r in m: by_pos.setdefault(r["pilot_id"], []).append(r["text"])
        multi = {k: v for k, v in by_pos.items() if len(v) > 1}
        summary.append(dict(model=model, generations=len(m), truncated=sum(r["truncated"] for r in m),
                            empty_text=sum(not r["text"] for r in m),
                            mean_seconds=round(statistics.mean(r["seconds"] for r in m), 1) if m else None,
                            mean_tokens_per_s=round(statistics.mean(r["tokens_per_s"] for r in m if r["tokens_per_s"]), 2) if m else None,
                            median_words=statistics.median(len(r["text"].split()) for r in m) if m else None,
                            positions_with_repeats=len(multi),
                            positions_identical_across_repeats=sum(len(set(v)) == 1 for v in multi.values())))
        for k, v in sorted(by_pos.items()):
            variation.append(dict(model=model, pilot_id=k, repeats=len(v), distinct_texts=len(set(v))))
    for name, table in (("summary.csv", summary), ("variation.csv", variation)):
        with (out_root / name).open("w", encoding="utf-8", newline="") as f:
            w = csv.DictWriter(f, fieldnames=list(table[0])); w.writeheader(); w.writerows(table)
    with (out_root / "review_sheet.csv").open("w", encoding="utf-8", newline="") as f:
        w = csv.writer(f); w.writerow(["pilot_id", "model", "repeat", "text", "n_claims", "n_incorrect", "quality_1to5", "notes"])
        for r in sorted((r for r in recs if r["repeat"] == 1), key=lambda r: (r["pilot_id"], r["model"])):
            w.writerow([r["pilot_id"], r["model"], r["repeat"], r["text"], "", "", "", ""])
    print("\n" + "\n".join(json.dumps(s) for s in summary))
    print(f"\nWrote tables to {out_root}")


if __name__ == "__main__":
    main()
