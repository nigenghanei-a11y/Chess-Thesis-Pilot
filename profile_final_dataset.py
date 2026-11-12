"""KPIs OF THE FINAL DATASET - what is in the rows you actually keep (after the Stockfish check).

Reads, from one run folder (outputs/elite_count/<run>/):
    01_candidates.jsonl    the exported candidates
    02_all_analyzed.jsonl  every candidate with Stockfish results and rejection reasons
    02_positions.jsonl     the positions that passed (your final dataset)
Writes next to them:
    final_dataset_kpis.txt  everything printed below
    final_dataset_kpis.json the same numbers for tables in the thesis

Run (project folder, venv active):
    python profile_final_dataset.py                    # the only run folder that has 02_positions.jsonl
    python profile_final_dataset.py --run 2026-10_E5_all
"""
import argparse, json, statistics, sys
from collections import Counter
from pathlib import Path

try:
    import config as C
    ROOT = C.ROOT
except Exception:
    ROOT = Path(".")


def load(p):
    return [json.loads(l) for l in open(p, encoding="utf-8") if l.strip()]


def num(vals):
    vals = [v for v in vals if v is not None]
    if not vals:
        return {"n": 0}
    return {"n": len(vals), "min": min(vals), "q1": statistics.quantiles(vals, n=4)[0] if len(vals) > 1 else vals[0],
            "median": statistics.median(vals), "mean": round(statistics.mean(vals), 1),
            "q3": statistics.quantiles(vals, n=4)[2] if len(vals) > 1 else vals[0], "max": max(vals)}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--run", default=None)
    a = ap.parse_args()
    base = ROOT / "outputs" / "elite_count"
    runs = sorted(p.name for p in base.glob("*") if (p / "02_positions.jsonl").exists()) if base.exists() else []
    run = a.run or (runs[0] if len(runs) == 1 else None)
    if not run:
        sys.exit("Choose a run with --run NAME. Available: " + (", ".join(runs) or "none"))
    rd = base / run
    cands, allrows, pos = load(rd / "01_candidates.jsonl"), load(rd / "02_all_analyzed.jsonl"), load(rd / "02_positions.jsonl")

    lines, out = [], {}
    def say(s=""):
        print(s); lines.append(s)
    def table(title, counter, total=None):
        total = total or sum(counter.values())
        say(f"  {title}")
        for k, v in sorted(counter.items(), key=lambda kv: str(kv[0])):
            say(f"    {str(k):<28}{v:>6}  ({100*v/total:5.1f}%)")

    say(f"FINAL DATASET KPIs   run folder: {rd}")
    say("")
    say("1. FUNNEL")
    rej = Counter()
    for r in allrows:
        for x in r.get("rejected_reasons", []):
            rej[x] += 1
    rejected = [r for r in allrows if r.get("rejected_reasons")]
    say(f"  candidates exported (01)        : {len(cands)}")
    say(f"  analysed with Stockfish (02)    : {len(allrows)}")
    say(f"  rejected by Stockfish check     : {len(rejected)}  reasons (a row can have several): {dict(rej)}")
    say(f"  FINAL positions kept            : {len(pos)}")
    out["funnel"] = {"candidates": len(cands), "analysed": len(allrows), "rejected": len(rejected), "reasons": dict(rej), "final": len(pos)}

    say("")
    say("2. COLUMNS in 02_positions.jsonl (non-empty count)")
    cols = Counter()
    for r in pos:
        for k, v in r.items():
            if v not in (None, "", []):
                cols[k] += 1
    for k in pos[0]:
        say(f"    {k:<28}{cols[k]:>5} / {len(pos)}")
    out["columns"] = {k: cols[k] for k in pos[0]}

    say("")
    say("3. PLAYERS AND GAMES")
    lo = [min(r["white_elo"], r["black_elo"]) for r in pos]
    hi = [max(r["white_elo"], r["black_elo"]) for r in pos]
    gap = [abs(r["white_elo"] - r["black_elo"]) for r in pos]
    for name, v in (("lower Elo of the two players", lo), ("higher Elo", hi), ("Elo gap", gap)):
        s = num(v); say(f"  {name:<30} min {s['min']}  Q1 {s['q1']}  median {s['median']}  mean {s['mean']}  Q3 {s['q3']}  max {s['max']}")
        out[name] = s
    say(f"  distinct games (game_id)       : {len({r['game_id'] for r in pos})} of {len(pos)} rows")
    say(f"  distinct positions (board)     : {len({' '.join(r['fen_before'].split()[:4]) for r in pos})} of {len(pos)} rows")
    table("time controls", Counter(r["time_control"] for r in pos))
    table("game result (White's view)", Counter(r["result"] for r in pos))
    table("side to move", Counter(r["side_to_move"] for r in pos))

    say("")
    say("4. POSITIONS")
    fm = [r["ply"] // 2 + 1 for r in pos]
    s = num(fm); say(f"  full move number               min {s['min']}  median {s['median']}  mean {s['mean']}  max {s['max']}")
    table("full-move bins", Counter(f"{(m // 5) * 5}-{(m // 5) * 5 + 4}" for m in fm))
    pieces = [sum(ch.isalpha() for ch in r["fen_before"].split()[0]) for r in pos]
    s = num(pieces); say(f"  pieces on the board            min {s['min']}  median {s['median']}  mean {s['mean']}  max {s['max']}")
    table("phase by piece count", Counter("<=12 pieces" if p <= 12 else "13-20 pieces" if p <= 20 else ">20 pieces" for p in pieces))
    out["full_move"], out["pieces"] = num(fm), num(pieces)

    say("")
    say("5. THE CANDIDATE MOVES (the human move that will be explained)")
    def kind(san):
        return ("castling" if san.startswith("O-O") else "pawn move" if san[0].islower() else "piece move")
    table("move type", Counter(kind(r["move_san"]) for r in pos))
    say(f"  captures                       {sum('x' in r['move_san'] for r in pos)}")
    say(f"  checks                         {sum(r['move_san'].endswith(('+', '#')) for r in pos)}")
    say(f"  promotions                     {sum('=' in r['move_san'] for r in pos)}")
    best = sum(bool(r.get("human_move_is_engine_best")) for r in pos)
    say(f"  human move = Stockfish best    {best} of {len(pos)} ({100*best/len(pos):.1f}%)")
    cl = [r["cp_loss"] for r in pos]
    s = num(cl); say(f"  Stockfish cp loss              min {s['min']}  median {s['median']}  mean {s['mean']}  max {s['max']}")
    table("cp loss bins", Counter("0" if c == 0 else "1-10" if c <= 10 else "11-20" if c <= 20 else "21-30" for c in cl))
    ev = [abs(r["eval_before_cp"]) for r in pos]
    s = num(ev); say(f"  |eval before| (cp)             min {s['min']}  median {s['median']}  mean {s['mean']}  max {s['max']}")
    table("|eval before| bins", Counter(f"{(e // 100) * 100}-{(e // 100) * 100 + 99}" for e in ev))
    say(f"  engine best line length (plies) min {min(len(r['engine_best_pv_san']) for r in pos)}  max {max(len(r['engine_best_pv_san']) for r in pos)}")
    say(f"  line after the move (plies)     min {min(len(r['pv_after_san']) for r in pos)}  max {max(len(r['pv_after_san']) for r in pos)}")
    out["cp_loss"], out["abs_eval_before"] = num(cl), num(ev)
    out["human_move_is_engine_best"] = best

    say("")
    say("6. AGREEMENT: Lichess evaluation vs YOUR Stockfish (same position, White's view, cp)")
    diffs = [abs(r["lichess_eval_before_cp"] - r["eval_before_cp"]) for r in pos
             if r.get("lichess_eval_before_cp") is not None and r.get("eval_before_cp") is not None]
    if diffs:
        s = num(diffs)
        say(f"  |difference| before the move   median {s['median']}  mean {s['mean']}  max {s['max']}  within 50 cp: {sum(x <= 50 for x in diffs)} of {len(diffs)}")
        out["eval_agreement"] = s
    say(f"  depth reached by Stockfish      {dict(Counter(r.get('depth_reached') for r in pos))}")

    (rd / "final_dataset_kpis.txt").write_text("\n".join(lines), encoding="utf-8")
    (rd / "final_dataset_kpis.json").write_text(json.dumps(out, indent=1), encoding="utf-8")
    say("")
    say(f"Saved: {rd / 'final_dataset_kpis.txt'} and final_dataset_kpis.json")


if __name__ == "__main__":
    main()
