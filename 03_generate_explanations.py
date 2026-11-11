"""STEP 3 - Generate explanations for conditions A, B, C with a local LLM (Ollama).

  A = position + candidate move                       (no engine information)
  B = A + Stockfish evaluation after the candidate move
  C = B + Stockfish principal variation (PV) that continues from the candidate move

B and C are identical except for the PV line, so the PV effect is isolated.
Condition D (repair of A) is NOT made here: it needs A's claims to be extracted and verified first.

Model, prompt skeleton and generation settings are the same for every condition (see config.py).
Results are appended to outputs/03_explanations.jsonl as they are produced, so an interrupted
run can be resumed by running the script again (finished position/condition pairs are skipped).

Usage:  python 03_generate_explanations.py
        python 03_generate_explanations.py --run-note "hybrid CPU/GPU, RTX 3050 6GB"
        python 03_generate_explanations.py --force        (regenerate everything)
"""
import argparse, json, subprocess, time
from pathlib import Path

import chess

import config as C
from common import sha256_file, write_manifest

CONDITIONS = ("A", "B", "C")


def fmt_eval(cp):
    """Centipawns (White's point of view) -> '+0.35'."""
    return f"{cp / 100:+.2f}"


def numbered_line(fen, sans):
    """['Bb5','a6','Ba4'] from a FEN -> '12. Bb5 a6 13. Ba4' (adds '...' if Black starts)."""
    board, out = chess.Board(fen), []
    for i, san in enumerate(sans):
        move = board.parse_san(san)
        if board.turn == chess.WHITE:
            out.append(f"{board.fullmove_number}.")
        elif i == 0:
            out.append(f"{board.fullmove_number}...")
        out.append(san)
        board.push(move)
    return " ".join(out)


def build_prompt(pos, cond):
    side, san = pos["side_to_move"], pos["move_san"]
    lines = [f"Position (FEN): {pos['fen_before']}",
             f"Side to move: {side}",
             f"Candidate move: {san}"]
    if cond in ("B", "C"):
        lines.append("Stockfish evaluation after the candidate move: "
                     f"{fmt_eval(pos['eval_after_cp'])} pawns "
                     "(positive = White is better, negative = Black is better).")
    if cond == "C":
        line = numbered_line(pos["fen_before"], [san] + list(pos["pv_after_san"]))
        lines.append(f"Stockfish principal variation starting with the candidate move: {line}")
    task = (f"Explain why {side} might consider playing {san}. "
            "Give a short, factual explanation (3 to 5 sentences) "
            "and avoid unsupported tactical claims.")
    return "\n".join(lines) + "\n\n" + task


def model_digest(client):
    try:
        for m in client.list()["models"]:
            if m["model"] == C.LLM_MODEL:
                return m["digest"]
    except Exception:
        pass
    return "unknown"


def ollama_cli_version():
    try:
        return subprocess.run(["ollama", "--version"], capture_output=True, text=True,
                              timeout=15).stdout.strip()
    except Exception:
        return "unknown"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--force", action="store_true", help="regenerate everything")
    ap.add_argument("--run-note", default="", help="free text stored in the manifest, e.g. hardware")
    args = ap.parse_args()

    import ollama  # imported here so the prompt builder can be tested without Ollama installed

    pos_path = C.OUT_DIR / "02_positions.jsonl"
    positions = [json.loads(l) for l in open(pos_path, encoding="utf-8")]
    out_path = C.OUT_DIR / "03_explanations.jsonl"

    done = set()
    if out_path.exists() and not args.force:
        for l in open(out_path, encoding="utf-8"):
            r = json.loads(l)
            done.add((r["pos_id"], r["condition"]))
    elif out_path.exists():
        out_path.unlink()

    client = ollama.Client()
    digest = model_digest(client)
    print(f"Model: {C.LLM_MODEL} | digest {digest[:12]} | temp {C.LLM_TEMPERATURE} "
          f"| seed {C.LLM_SEED} | num_ctx {C.LLM_NUM_CTX}")

    total = len(positions) * len(CONDITIONS)
    n_new = 0
    with open(out_path, "a", encoding="utf-8") as f:
        for pos in positions:
            for cond in CONDITIONS:
                if (pos["pos_id"], cond) in done:
                    continue
                prompt = build_prompt(pos, cond)
                t0 = time.time()
                resp = client.chat(
                    model=C.LLM_MODEL,
                    messages=[{"role": "user", "content": prompt}],
                    options={"temperature": C.LLM_TEMPERATURE, "seed": C.LLM_SEED,
                             "num_predict": C.LLM_MAX_TOKENS, "num_ctx": C.LLM_NUM_CTX},
                )
                secs = round(time.time() - t0, 1)
                row = {"pos_id": pos["pos_id"], "cand_id": pos["cand_id"], "condition": cond,
                       "fen_before": pos["fen_before"], "move_san": pos["move_san"],
                       "prompt": prompt, "explanation": resp["message"]["content"].strip(),
                       "done_reason": resp.get("done_reason"),
                       "truncated": resp.get("done_reason") == "length",
                       "prompt_tokens": resp.get("prompt_eval_count"),
                       "output_tokens": resp.get("eval_count"), "seconds": secs}
                f.write(json.dumps(row) + "\n")
                f.flush()
                n_new += 1
                flag = "  [TRUNCATED]" if row["truncated"] else ""
                print(f"{pos['pos_id']} {cond}  {secs:>6}s  {row['output_tokens']} tokens{flag}")

    rows = [json.loads(l) for l in open(out_path, encoding="utf-8")]
    write_manifest(C.OUT_DIR / "03_manifest.json", "03_generate_explanations",
                   {"model": C.LLM_MODEL, "model_digest": digest,
                    "ollama_cli_version": ollama_cli_version(),
                    "conditions": list(CONDITIONS),
                    "input_positions_sha256": sha256_file(pos_path),
                    "script_sha256": sha256_file(Path(__file__)),
                    "run_note": args.run_note,
                    "explanations_total": len(rows), "generated_this_run": n_new,
                    "truncated": sum(r["truncated"] for r in rows),
                    "expected_total": total})
    print(f"\n{len(rows)}/{total} explanations in {out_path}")
    if any(r["truncated"] for r in rows):
        print("WARNING: some outputs hit LLM_MAX_TOKENS and were cut off - check them before extracting claims.")


if __name__ == "__main__":
    main()
