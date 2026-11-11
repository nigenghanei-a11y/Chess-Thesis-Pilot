"""STEP 1 - Reproducible sampling of candidate positions from one pinned Lichess monthly file.

Single streaming pass with seeded reservoir sampling over games that pass the game-level
criteria, then one random eligible position per sampled game.
Same file + same config + same seed  ->  identical output.

Usage:  python 01_sample_candidates.py              (downloads the file if missing)
        python 01_sample_candidates.py --input path/to/file.pgn.zst
"""
import argparse, io, json, random
from pathlib import Path

import chess, chess.pgn, requests, zstandard

import config as C
from common import sha256_file, write_manifest


def download(url, dest):
    dest.parent.mkdir(parents=True, exist_ok=True)
    print(f"Downloading {url}")
    with requests.get(url, stream=True, timeout=60) as r:
        r.raise_for_status()
        with open(dest, "wb") as f:
            for chunk in r.iter_content(1 << 20):
                f.write(chunk)


def est_seconds(tc):
    """'600+5' -> 600 + 40*5. Returns None for '-' (correspondence) or malformed values."""
    try:
        base, inc = tc.split("+")
        return int(base) + 40 * int(inc)
    except (ValueError, AttributeError):
        return None


def game_ok(h):
    try:
        w, b = int(h["WhiteElo"]), int(h["BlackElo"])
    except (KeyError, ValueError):
        return False
    if not (C.ELO_MIN <= w <= C.ELO_MAX and C.ELO_MIN <= b <= C.ELO_MAX):
        return False
    secs = est_seconds(h.get("TimeControl", ""))
    if secs is None or secs < C.MIN_EST_SECONDS:
        return False
    if C.REQUIRE_NORMAL_TERMINATION and h.get("Termination") != "Normal":
        return False
    return h.get("Result") in {"1-0", "0-1", "1/2-1/2"}


def stream_reservoir(path, k, rng):
    """Uniform random sample of k eligible games, in one pass, deterministic given rng."""
    reservoir, seen, eligible = [], 0, 0
    dctx = zstandard.ZstdDecompressor(max_window_size=2**31)
    with open(path, "rb") as fh, dctx.stream_reader(fh) as reader:
        text = io.TextIOWrapper(reader, encoding="utf-8", errors="replace")
        while (game := chess.pgn.read_game(text)) is not None:
            seen += 1
            if seen % 20000 == 0:
                print(f"  ...{seen:,} games read, {eligible:,} eligible")
            h = game.headers
            if not game_ok(h):
                continue
            moves = [m.uci() for m in game.mainline_moves()]
            if len(moves) < C.MIN_PLIES:
                continue
            eligible += 1
            item = {"game_id": h.get("Site", ""), "white_elo": int(h["WhiteElo"]),
                    "black_elo": int(h["BlackElo"]), "time_control": h["TimeControl"],
                    "result": h["Result"], "moves": moves}
            if len(reservoir) < k:
                reservoir.append(item)
            else:
                j = rng.randrange(eligible)
                if j < k:
                    reservoir[j] = item
    return reservoir, seen, eligible


def pick_position(moves, rng):
    """Replay the game; choose one random ply that satisfies the position-level criteria."""
    board, options = chess.Board(), []
    for ply, uci in enumerate(moves):
        if (C.FULLMOVE_MIN <= board.fullmove_number <= C.FULLMOVE_MAX
                and not board.is_check() and board.legal_moves.count() >= 2):
            options.append((ply, board.fen(), uci, board.san(chess.Move.from_uci(uci))))
        board.push_uci(uci)
    return rng.choice(options) if options else None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--input", type=Path, default=C.RAW_DIR / C.LICHESS_FILE)
    args = ap.parse_args()

    if not args.input.exists():
        download(C.LICHESS_URL, args.input)
    digest = sha256_file(args.input)
    print(f"Input: {args.input.name}  sha256={digest[:16]}...")

    rng = random.Random(C.SEED)
    reservoir, seen, eligible = stream_reservoir(args.input, C.N_CANDIDATES, rng)
    rng.shuffle(reservoir)

    C.OUT_DIR.mkdir(parents=True, exist_ok=True)
    rows = []
    for g in reservoir:
        pick = pick_position(g["moves"], rng)
        if pick is None:
            continue
        ply, fen, uci, san = pick
        rows.append({"cand_id": f"C{len(rows)+1:03d}", "game_id": g["game_id"],
                     "white_elo": g["white_elo"], "black_elo": g["black_elo"],
                     "time_control": g["time_control"], "result": g["result"],
                     "ply": ply, "fen_before": fen, "move_uci": uci, "move_san": san,
                     "side_to_move": "White" if " w " in fen else "Black"})

    out = C.OUT_DIR / "01_candidates.jsonl"
    with open(out, "w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r) + "\n")

    write_manifest(C.OUT_DIR / "01_manifest.json", "01_sample_candidates",
                   {"input_file": args.input.name, "input_sha256": digest,
                    "games_read": seen, "games_eligible": eligible,
                    "candidates_written": len(rows)})
    print(f"Games read: {seen:,} | eligible: {eligible:,} | candidates written: {len(rows)} -> {out}")


if __name__ == "__main__":
    main()
