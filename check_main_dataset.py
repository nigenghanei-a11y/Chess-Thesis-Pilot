"""Validate outputs/step1_pilot/<month>/positions_main.csv (run after the main-dataset cell in step1_pilot.ipynb).

    python check_main_dataset.py

Prints OK / FLAG / INFO lines, writes main_checks.json next to the data, and lists the position IDs behind every FLAG.
It changes no data file.
"""
import json, math
import chess
import pandas as pd
import config

D = config.OUT_DIR / "step1_pilot" / config.MONTH
main = pd.read_csv(D / "positions_main.csv")
key = pd.read_csv(D / "pilot_key.csv")
report, flags, ids = {}, [], {}


def check(name, value, ok, bad_ids=None):
    report[name] = value
    print(f"{'OK  ' if ok else 'FLAG'} {name}: {value}")
    if not ok:
        flags.append(name)
        if bad_ids is not None: ids[name] = list(map(str, bad_ids))


def info(name, value):
    report[name] = value
    print(f"INFO {name}: {value}")


def winprob(cp): return 0.5 + 0.5 * (2 / (1 + math.exp(-0.00368208 * cp)) - 1)


kinds = main.kind.value_counts().to_dict()
info("rows / games / by kind", f"{len(main)} / {main.game_id.nunique()} / {kinds}")
check("position_id unique", int(main.position_id.duplicated().sum()), not main.position_id.duplicated().any())
check("at most one position per game and kind", int(main.groupby(["game_id", "kind"]).size().max()), main.groupby(["game_id", "kind"]).size().max() == 1)
check("pilot games absent from main", len(set(main.game_id) & set(key.game_id)), not set(main.game_id) & set(key.game_id))
check("pilot FENs absent from main", len(set(main.fen) & set(key.fen)), not set(main.fen) & set(key.fen))
pk = main.fen.str.split().str[:4].str.join(" ")
check("duplicate board positions in main", int(pk.duplicated().sum()), not pk.duplicated().any(), main.position_id[pk.duplicated()])

good, bad = main.kind == "good", main.kind == "bad"
check("good: cp_loss <= GOOD_MAX_CP_LOSS", int((good & (main.stockfish_cp_loss > config.GOOD_MAX_CP_LOSS)).sum()),
      not (good & (main.stockfish_cp_loss > config.GOOD_MAX_CP_LOSS)).any(), main.position_id[good & (main.stockfish_cp_loss > config.GOOD_MAX_CP_LOSS)])
check("bad: cp_loss >= BAD_MIN_CP_LOSS", int((bad & (main.stockfish_cp_loss < config.BAD_MIN_CP_LOSS)).sum()),
      not (bad & (main.stockfish_cp_loss < config.BAD_MIN_CP_LOSS)).any(), main.position_id[bad & (main.stockfish_cp_loss < config.BAD_MIN_CP_LOSS)])
check("kind == stockfish_label", int((main.kind != main.stockfish_label).sum()), (main.kind == main.stockfish_label).all())
s = main.side.map({"white": 1, "black": -1})
recomputed = (s * main.eval_before_cp - s * main.eval_after_cp).clip(lower=0)
check("cp_loss recomputes from evals", int((recomputed != main.stockfish_cp_loss).sum()), (recomputed == main.stockfish_cp_loss).all(), main.position_id[recomputed != main.stockfish_cp_loss])
check("|eval before| <= MAX_ABS_EVAL_CP", int((main.eval_before_cp.abs() > config.MAX_ABS_EVAL_CP).sum()), (main.eval_before_cp.abs() <= config.MAX_ABS_EVAL_CP).all())
check("fullmove within window", int((~main.fullmove.between(config.FULLMOVE_MIN, config.FULLMOVE_MAX)).sum()), main.fullmove.between(config.FULLMOVE_MIN, config.FULLMOVE_MAX).all())
check("winprob_drop is exact (not rounded)", bool((main.stockfish_winprob_drop.round(1) != main.stockfish_winprob_drop).any()),
      bool((main.stockfish_winprob_drop.round(1) != main.stockfish_winprob_drop).any()))

# FEN / SAN integrity and board features
fails, caps, nlegal = [], [], []
for r in main.itertuples():
    try:
        b = chess.Board(r.fen)
        mv = b.parse_san(r.move_san)
        assert mv.uci() == r.move_uci and b.parse_san(r.engine_best_san)
        caps.append(b.is_capture(mv)); nlegal.append(b.legal_moves.count())
    except Exception:
        fails.append(r.position_id); caps.append(None); nlegal.append(None)
check("FEN + SAN + UCI parse and agree", len(fails), not fails, fails)
main["is_capture"], main["n_legal"] = caps, nlegal

# THE KEY CHECK: a 'bad' move that Stockfish itself recommends is contradictory
bad_best = main[bad & main.human_move_is_engine_best]
check("bad positions whose played move IS Stockfish's best move", len(bad_best), bad_best.empty, bad_best.position_id)
good_not_best = main[good & ~main.human_move_is_engine_best]
info("good positions whose move is not engine-best (<=30 cp, acceptable)", len(good_not_best))
# bad moves where the best move is also near-identical would need MultiPV; report how large the gaps are
info("bad cp_loss quartiles", main[bad].stockfish_cp_loss.quantile([.25, .5, .75]).round(0).to_dict())

# trivial-position risk
for k in ("good", "bad"):
    m = main[main.kind == k]
    info(f"{k}: share captures / median legal moves / share <=10 legal moves",
         f"{m.is_capture.mean():.2f} / {m.n_legal.median():.0f} / {(m.n_legal <= 10).mean():.2f}")

# independence: top players repeat
g = main.drop_duplicates("game_id")
counts = pd.concat([g.white, g.black]).value_counts()
top5 = counts.head(5)
share = float(((g.white.isin(top5.index)) | (g.black.isin(top5.index))).mean())
info("unique players / top-5 players (games)", f"{len(counts)} / {top5.to_dict()}")
info("share of games involving a top-5 player", round(share, 2))
info("games with both a good and a bad position", int(main.groupby("game_id").kind.nunique().eq(2).sum()))
info("time controls", main.time_control.value_counts().head(4).to_dict())

report["flags"], report["flagged_ids"] = flags, ids
(D / "main_checks.json").write_text(json.dumps(report, indent=2, default=str) + "\n", encoding="utf-8")
print("\nALL CHECKS PASSED" if not flags else f"\nFLAGS: {flags}\nIDs: {json.dumps(ids)[:1500]}")
