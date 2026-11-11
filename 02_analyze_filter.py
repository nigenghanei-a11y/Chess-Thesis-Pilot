"""STEP 2 - Stockfish analysis of the candidates + engine-based filtering -> final pilot positions.

For each candidate (position before the human move, human move):
  * analyse the position BEFORE the move  -> engine best move, eval_before
  * analyse the position AFTER the move   -> eval_after, PV continuing from the candidate move
  * cp_loss = how much the human move lost compared with the engine's best move (mover's view)
Filters (config): no mate scores, |eval_before| <= MAX_ABS_EVAL_CP, cp_loss <= MAX_CP_LOSS.
The first N_PILOT candidates that pass (in file order) become P01..P10.

All evals are stored in centipawns from WHITE's point of view (positive = White better).
Usage: python 02_analyze_filter.py
"""
import json, shutil, sys
from pathlib import Path

import chess, chess.engine

import config as C
from common import sha256_file, write_manifest


def find_stockfish():
    p = Path(C.STOCKFISH_PATH)
    if not p.is_absolute():
        p = C.ROOT / p
    if p.exists():
        return str(p)
    found = sorted((C.ROOT / "stockfish").rglob("stockfish*.exe")) if (C.ROOT / "stockfish").exists() else []
    if found:
        return str(found[0])
    on_path = shutil.which("stockfish")
    if on_path:
        return on_path
    sys.exit(f"Stockfish not found. Set STOCKFISH_PATH in config.py (tried: {p})")


def pv_to_san(board, pv, max_plies):
    b, out = board.copy(), []
    for mv in pv[:max_plies]:
        out.append(b.san(mv))
        b.push(mv)
    return out


def analyse(engine, board, tag):
    # distinct `game` token per call -> python-chess sends ucinewgame -> hash is cleared -> deterministic
    info = engine.analyse(board, chess.engine.Limit(depth=C.STOCKFISH_DEPTH),
                          multipv=C.STOCKFISH_MULTIPV, game=tag)
    if isinstance(info, list):
        info = info[0]
    return info


def main():
    cand_path = C.OUT_DIR / "01_candidates.jsonl"
    candidates = [json.loads(l) for l in open(cand_path, encoding="utf-8")]
    sf_path = find_stockfish()

    engine = chess.engine.SimpleEngine.popen_uci(sf_path)
    engine.configure({"Threads": C.STOCKFISH_THREADS, "Hash": C.STOCKFISH_HASH_MB})
    engine_name = engine.id.get("name", "unknown")
    print(f"Engine: {engine_name} | depth {C.STOCKFISH_DEPTH} | threads {C.STOCKFISH_THREADS}")

    analysed, kept = [], []
    try:
        for c in candidates:
            board = chess.Board(c["fen_before"])
            mover = board.turn
            move = chess.Move.from_uci(c["move_uci"])

            before = analyse(engine, board, f"{c['cand_id']}_before")
            best_move = before["pv"][0]
            best_san = board.san(best_move)
            best_pv_san = pv_to_san(board, before["pv"], C.PV_MAX_PLIES)

            after_board = board.copy()
            after_board.push(move)
            after = analyse(engine, after_board, f"{c['cand_id']}_after")

            row = dict(c, engine_best_uci=best_move.uci(), engine_best_san=best_san,
                       engine_best_pv_san=best_pv_san,
                       human_move_is_engine_best=(best_move == move))

            reasons = []
            if before["score"].is_mate() or after["score"].is_mate():
                reasons.append("mate_score")
            else:
                eval_before = before["score"].white().score()
                eval_after = after["score"].white().score()
                cp_loss = max(0, before["score"].pov(mover).score() - after["score"].pov(mover).score())
                row.update(eval_before_cp=eval_before, eval_after_cp=eval_after, cp_loss=cp_loss,
                           pv_after_san=pv_to_san(after_board, after.get("pv", []), C.PV_MAX_PLIES),
                           depth_reached=after.get("depth"))
                if abs(eval_before) > C.MAX_ABS_EVAL_CP:
                    reasons.append("already_decided")
                if cp_loss > C.MAX_CP_LOSS:
                    reasons.append("cp_loss_too_high")

            row["rejected_reasons"] = reasons
            analysed.append(row)
            status = "KEEP" if not reasons and len(kept) < C.N_PILOT else ("reject:" + ",".join(reasons) if reasons else "spare")
            print(f"{c['cand_id']} {c['move_san']:<7} {status}")
            if not reasons and len(kept) < C.N_PILOT:
                kept.append(dict(row, pos_id=f"P{len(kept)+1:02d}"))
            if len(kept) >= C.N_PILOT:
                break
    finally:
        engine.quit()

    with open(C.OUT_DIR / "02_all_analyzed.jsonl", "w", encoding="utf-8") as f:
        for r in analysed:
            f.write(json.dumps(r) + "\n")
    with open(C.OUT_DIR / "02_positions.jsonl", "w", encoding="utf-8") as f:
        for r in kept:
            f.write(json.dumps(r) + "\n")

    write_manifest(C.OUT_DIR / "02_manifest.json", "02_analyze_filter",
                   {"engine_name": engine_name, "engine_sha256": sha256_file(sf_path),
                    "input_candidates_sha256": sha256_file(cand_path),
                    "candidates_analysed": len(analysed), "positions_kept": len(kept)})
    print(f"\nAnalysed {len(analysed)} | kept {len(kept)} -> outputs/02_positions.jsonl")
    if len(kept) < C.N_PILOT:
        print(f"WARNING: only {len(kept)} passed; increase N_CANDIDATES in config.py and rerun step 1.")


if __name__ == "__main__":
    main()
