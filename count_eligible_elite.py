"""ELITE count + candidate export: the strongest games, and only positions where the human move was good.

ONE streaming pass over a Lichess monthly file:
  1. Counts games under a baseline (your config) and five ELITE tiers (both players >= 2200 ... 2600).
  2. Reads Lichess's own server evaluations ([%eval ...]) and computes, for every position at full move
     10-30, how many centipawns the played move lost (cp_loss, mover's point of view). A position-move
     pair is "good" if cp_loss <= LOSS_MAX, |eval before| <= MAX_ABS_EVAL_CP and there is no mate score.
  3. Lists the highest-rated games that contain at least one good pair.
  4. Exports candidates (same format as your 01_candidates.jsonl) from the STRICTEST tier that still has at
     least --min-pool games: a seeded random sample of N_CANDIDATES games, one good position per game.
     Your step 02 then re-checks every move with YOUR Stockfish (the Lichess eval is only a pre-filter).

Everything goes into its own run folder:   outputs/elite_count/<run name>/
    run_log.txt          everything printed to the screen (with timestamps)
    elite_counts.json    results + reproducibility manifest + results_sha256
    01_candidates.jsonl  the exported rows (steps 02/03 read this; use run_elite_step.py, no copying)
    checkpoint.pkl       only while a run is unfinished

RE-RUNNABLE: press Ctrl+C at any time (or lose power) and run the SAME command again; it continues from the
last checkpoint (every --checkpoint-every games). The result is identical to an uninterrupted run
(same results_sha256). Use --fresh to ignore a checkpoint and start over.

Run (VS Code terminal, project folder, venv active):
  python count_eligible_elite.py --month 2026-10 --max-games 3000000        # quick estimate
  python count_eligible_elite.py --input data\\raw\\lichess_db_standard_rated_2026-10.pgn.zst   # full file
"""
import argparse, hashlib, heapq, io, json, os, pickle, platform, random, re, sys, time
from importlib import metadata
from pathlib import Path

import chess, requests, zstandard

try:
    import config as C
    ROOT = C.ROOT
    CUR = dict(elo_min=C.ELO_MIN, elo_max=C.ELO_MAX, min_est=C.MIN_EST_SECONDS, min_plies=C.MIN_PLIES,
               fm_min=C.FULLMOVE_MIN, fm_max=C.FULLMOVE_MAX, seed=C.SEED, n_cand=C.N_CANDIDATES,
               max_abs_eval=C.MAX_ABS_EVAL_CP, loss_max=C.MAX_CP_LOSS)
except Exception:                                  # also works standalone
    ROOT = Path(".")
    CUR = dict(elo_min=1800, elo_max=2200, min_est=480, min_plies=60, fm_min=10, fm_max=30, seed=42,
               n_cand=40, max_abs_eval=500, loss_max=30)

STRICT_LOSS = 10          # second, stricter definition of "good move" that is always reported
MAX_GAP = 100             # |white Elo - black Elo| for elite tiers
ELITE_MIN_EST = 480       # rapid or slower (base + 40*increment, seconds)
RESERVOIR = 400           # games kept per tier for the seeded export sample
TOP_K = 50                # length of the "highest-rated games" list

TIERS = {
    "B0": dict(label="baseline: your current config (no eval needed)", elo_min=CUR["elo_min"], elo_max=CUR["elo_max"],
               max_gap=None, min_est=CUR["min_est"], min_plies=CUR["min_plies"], no_bot=False, need_eval=False),
}
for _k, _e in (("E1", 2200), ("E2", 2300), ("E3", 2400), ("E4", 2500), ("E5", 2600)):
    TIERS[_k] = dict(label=f"elite: both >= {_e}, gap <= {MAX_GAP}, rapid+, eval, no bots", elo_min=_e, elo_max=None,
                     max_gap=MAX_GAP, min_est=ELITE_MIN_EST, min_plies=CUR["min_plies"], no_bot=True, need_eval=True)
ELITE = [k for k in TIERS if k != "B0"]          # ordered loosest -> strictest
STAGES = ["elo", "gap", "time", "normal+result+bot", "plies", "has_eval", "good_move_position"]
WANT = {b"WhiteElo", b"BlackElo", b"TimeControl", b"Termination", b"Result", b"WhiteTitle", b"BlackTitle", b"Site", b"White", b"Black"}
STRIP = re.compile(r"\{[^}]*\}|\d+\.(?:\.\.)?|\$\d+|1-0|0-1|1/2-1/2|\*")
TOK = re.compile(r"\{([^}]*)\}|\d+\.(?:\.\.)?|\$\d+|(1-0|0-1|1/2-1/2|\*)|(\S+)")
EVAL = re.compile(r"\[%eval ([^\]\s]+)\]")

LOGFILE = None


def log(msg=""):
    """Print AND append to run_log.txt, so you can always see afterwards what happened."""
    line = f"[{time.strftime('%H:%M:%S')}] {msg}" if msg else ""
    print(line, flush=True)
    if LOGFILE:
        with open(LOGFILE, "a", encoding="utf-8") as fh:
            fh.write(line + "\n")


def fmt_time(sec):
    sec = int(max(0, sec)); h, r = divmod(sec, 3600); m, s = divmod(r, 60)
    return f"{h}h{m:02d}m" if h else f"{m}m{s:02d}s"


class HashingReader:
    """SHA-256 of exactly the compressed bytes consumed (whole file if read to the end)."""
    def __init__(self, raw):
        self.raw, self.h, self.n = raw, hashlib.sha256(), 0
    def read(self, size=-1):
        b = self.raw.read(size); self.h.update(b); self.n += len(b); return b


def est_seconds(tc):
    try:
        base, inc = tc.split("+"); return int(base) + 40 * int(inc)
    except (ValueError, AttributeError):
        return None


def open_stream(args):
    dctx = zstandard.ZstdDecompressor(max_window_size=2**31)
    if args.input:
        if not args.input.exists():
            sys.exit(f"ERROR: file not found: {args.input}")
        name, raw, source = args.input.name, open(args.input, "rb"), str(args.input)
        total = args.input.stat().st_size
        log(f"Opening local file {source}  ({total/1e9:.2f} GB compressed)")
    else:
        name = f"lichess_db_standard_rated_{args.month}.pgn.zst"
        source = f"https://database.lichess.org/standard/{name}"
        log(f"Connecting to {source} ...")
        r = requests.get(source, stream=True, timeout=60); r.raise_for_status(); raw = r.raw
        total = int(r.headers.get("Content-Length", 0)) or None
        log(f"Connected: HTTP {r.status_code}, size {'%.2f GB' % (total/1e9) if total else 'unknown'} (streaming, nothing saved to disk)")
    hr = HashingReader(raw)
    return name, source, total, hr, io.BufferedReader(dctx.stream_reader(hr), buffer_size=1 << 22)


def iter_games(f, skip=0, skip_tick=None):
    """Yield (header_dict, movetext_bytes). The first `skip` games are only counted (used when resuming)."""
    hdr, skipped = {}, 0
    for line in f:
        if skipped < skip:
            if line[:1] != b"[" and line.strip():
                skipped += 1
                if skip_tick and skipped % 500_000 == 0:
                    skip_tick(skipped)
            continue
        if line.startswith(b"["):
            sp = line.find(b" "); tag = line[1:sp]
            if tag in WANT:
                hdr[tag] = line[sp + 2: line.rfind(b'"')].decode("utf-8", "replace")
        elif line.strip():
            yield hdr, line
            hdr = {}


def parse_movetext(text):
    """-> (sans, evals). evals[i] = White-POV centipawns AFTER ply i, 'M' for mate scores, None if absent."""
    sans, evals = [], []
    for m in TOK.finditer(text):
        if m.group(1) is not None:
            if evals:
                e = EVAL.search(m.group(1))
                if e:
                    s = e.group(1)
                    evals[-1] = "M" if s.startswith("#") else int(round(float(s) * 100))
        elif m.group(2) is None and m.group(3) is not None:
            sans.append(m.group(3).rstrip("?!")); evals.append(None)
    return sans, evals


def good_plies(sans, evals, fm_min, fm_max, max_abs, loss_max):
    out = []
    for i in range(1, len(sans)):
        fm = i // 2 + 1
        if fm < fm_min or fm > fm_max:
            continue
        b, a = evals[i - 1], evals[i]
        if b is None or a is None or b == "M" or a == "M" or abs(b) > max_abs:
            continue
        loss = max(0, (b - a) if i % 2 == 0 else (a - b))    # mover's point of view
        if loss <= loss_max:
            out.append((i, b, a, loss))
    return out


def tier_stage(t, h, ctx, move_line):
    w, b = ctx["w"], ctx["b"]
    if not (t["elo_min"] <= w and t["elo_min"] <= b and (t["elo_max"] is None or (w <= t["elo_max"] and b <= t["elo_max"]))):
        return 0
    if t["max_gap"] is not None and abs(w - b) > t["max_gap"]:
        return 1
    if ctx["est"] is None or ctx["est"] < t["min_est"]:
        return 2
    if h.get(b"Termination") != "Normal" or h.get(b"Result") not in ("1-0", "0-1", "1/2-1/2"):
        return 3
    if t["no_bot"] and (h.get(b"WhiteTitle") == "BOT" or h.get(b"BlackTitle") == "BOT"):
        return 3
    if ctx["plies"](move_line) < t["min_plies"]:
        return 4
    if t["need_eval"] and b"[%eval" not in move_line:
        return 5
    return 6


def run_name_for(args, name):
    m = re.search(r"(\d{4}-\d{2})", name)
    return f"{m.group(1) if m else name}_{('first%d' % args.max_games) if args.max_games else 'full'}"


def save_checkpoint(path, state):
    tmp = str(path) + ".tmp"
    with open(tmp, "wb") as fh:
        pickle.dump(state, fh, protocol=4)
    os.replace(tmp, path)


def main():
    global LOGFILE
    ap = argparse.ArgumentParser()
    ap.add_argument("--month"); ap.add_argument("--input", type=Path)
    ap.add_argument("--max-games", type=int, default=0, help="0 = whole file")
    ap.add_argument("--loss-max", type=int, default=CUR["loss_max"], help="max cp loss for a 'good' move (default: config MAX_CP_LOSS)")
    ap.add_argument("--min-pool", type=int, default=3 * CUR["n_cand"], help="strictest tier with at least this many good-move games is used for the export")
    ap.add_argument("--n", type=int, default=CUR["n_cand"], help="number of candidates to export")
    ap.add_argument("--select", choices=["random", "top"], default="random")
    ap.add_argument("--max-per-player", type=int, default=2, help="max exported games per player (either colour); 0 = no cap")
    ap.add_argument("--out-dir", type=Path, default=None, help="base folder (default: <project>/outputs/elite_count)")
    ap.add_argument("--run-name", default=None, help="run folder name (default: <month>_first<N> or <month>_full)")
    ap.add_argument("--checkpoint-every", type=int, default=2_000_000, help="games between checkpoints")
    ap.add_argument("--progress-seconds", type=int, default=20, help="seconds between progress lines")
    ap.add_argument("--fresh", action="store_true", help="ignore an existing checkpoint and start from game 1")
    ap.add_argument("--stop-after", type=int, default=0, help=argparse.SUPPRESS)       # testing: simulate Ctrl+C
    a = ap.parse_args()
    if not (a.month or a.input):
        sys.exit("give --month YYYY-MM or --input file")

    base = a.out_dir if a.out_dir else ROOT / "outputs" / "elite_count"
    name_guess = a.input.name if a.input else f"lichess_db_standard_rated_{a.month}.pgn.zst"
    run_name = a.run_name or run_name_for(a, name_guess)
    run_dir = base / run_name
    run_dir.mkdir(parents=True, exist_ok=True)
    LOGFILE = run_dir / "run_log.txt"
    ckpt_path = run_dir / "checkpoint.pkl"
    final_json = run_dir / "elite_counts.json"
    script_sha = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()

    log("=" * 78)
    log(f"STEP 1/6  Settings   (run folder: {run_dir})")
    log(f"  input        : {a.input if a.input else 'Lichess ' + str(a.month) + ' (streamed)'}")
    log(f"  games to read: {'first %s' % format(a.max_games, ',') if a.max_games else 'whole file'}")
    log(f"  good move    : cp loss <= {a.loss_max} (also reported at <= {STRICT_LOSS}); |eval before| <= {CUR['max_abs_eval']}; "
        f"full moves {CUR['fm_min']}-{CUR['fm_max']}; source = Lichess [%eval]")
    log(f"  seed         : {CUR['seed']}   export: {a.n} games from the strictest tier with >= {a.min_pool} good-move games ({a.select})")
    log(f"  duplicates   : skip a position already exported (same board/turn/castling/en-passant); max {a.max_per_player or 'unlimited'} exported games per player")
    for k, t in TIERS.items():
        log(f"  tier {k}: {t['label']}")

    fingerprint = hashlib.sha256(json.dumps(
        {"script": script_sha, "tiers": TIERS, "seed": CUR["seed"], "loss": a.loss_max, "strict": STRICT_LOSS, "maxpp": a.max_per_player,
         "max_abs": CUR["max_abs_eval"], "win": [CUR["fm_min"], CUR["fm_max"]], "name": name_guess}, sort_keys=True).encode()).hexdigest()

    # ---------------- fresh state or checkpoint -------------------------------------------------------------
    seed = CUR["seed"]
    S = dict(read=0, reach={k: [0] * (len(STAGES) + 1) for k in TIERS}, pos_good={k: 0 for k in ELITE}, pos_strict={k: 0 for k in ELITE},
             games_strict={k: 0 for k in ELITE}, reservoirs={k: [] for k in ELITE}, seen_pool={k: 0 for k in ELITE},
             top_heap=[], top_payload={}, rng_states=None, evals_seen=0, fingerprint=fingerprint)
    rngs = {k: random.Random(f"{seed}-{k}") for k in ELITE}
    resume_from = 0
    if ckpt_path.exists() and not a.fresh:
        try:
            saved = pickle.load(open(ckpt_path, "rb"))
        except Exception as e:
            log(f"  checkpoint unreadable ({e}); starting fresh"); saved = None
        if saved and saved["fingerprint"] != fingerprint:
            sys.exit("ERROR: a checkpoint exists but was made with a different script/settings/input. "
                     "Re-run with --fresh to start over, or restore the original script.")
        if saved and a.max_games and saved["read"] > a.max_games:
            sys.exit(f"ERROR: checkpoint is at {saved['read']:,} games, beyond --max-games {a.max_games:,}. Use --fresh.")
        if saved:
            S = saved; resume_from = S["read"]
            for k in ELITE:
                rngs[k].setstate(S["rng_states"][k])
            log(f"  CHECKPOINT FOUND: resuming after {resume_from:,} games (counts, samples and random streams restored)")
    elif ckpt_path.exists():
        log("  --fresh given: ignoring existing checkpoint"); ckpt_path.unlink()
    log("")

    # ---------------- open input ----------------------------------------------------------------------------
    log("STEP 2/6  Opening the input")
    name, source, total_bytes, hr, f = open_stream(a)
    log("")
    log("STEP 3/6  Streaming games (this is the long step; progress line every %ds; Ctrl+C is safe)" % a.progress_seconds)
    if resume_from:
        log(f"  fast-forwarding past the first {resume_from:,} games (re-reading them only to rebuild the input hash; no analysis) ...")
        t_ff = time.time()
        def tick(n): log(f"    skipped {n:,}/{resume_from:,}  ({fmt_time(time.time()-t_ff)})")
        gen = iter_games(f, skip=resume_from, skip_tick=tick)
    else:
        gen = iter_games(f)

    t_live = time.time(); n_live0 = hr.n; read_live0 = S["read"]
    last_print = time.time(); last_ckpt = S["read"]; first_logged = False
    top_heap, top_payload = S["top_heap"], S["top_payload"]
    interrupted = False

    def checkpoint(reason):
        S["rng_states"] = {k: rngs[k].getstate() for k in ELITE}
        save_checkpoint(ckpt_path, S)
        log(f"  [checkpoint saved at {S['read']:,} games - {reason}]")

    try:
        for h, mv in gen:
            S["read"] += 1
            if not first_logged:
                first_logged = True
                log(f"  first live game read: {h.get(b'Site', '?')}  Elo {h.get(b'WhiteElo','?')}/{h.get(b'BlackElo','?')}  "
                    f"{h.get(b'TimeControl','?')}  -> parsing works")
            try:
                w, b = int(h[b"WhiteElo"]), int(h[b"BlackElo"])
            except (KeyError, ValueError):
                w = b = 0
            if b"[%eval" in mv:
                S["evals_seen"] += 1
            cache = {}
            ctx = {"w": w, "b": b, "est": est_seconds(h.get(b"TimeControl", "")),
                   "plies": lambda ml: cache.setdefault("p", len(STRIP.sub(" ", ml.decode("utf-8", "replace")).split()))}
            parsed = None
            for k, t in TIERS.items():
                st = tier_stage(t, h, ctx, mv)
                for i in range(min(st, 6) + 1):
                    S["reach"][k][i] += 1
                if st < 6 or k == "B0":
                    continue
                if parsed is None:
                    sans, evals = parse_movetext(mv.decode("utf-8", "replace"))
                    g = good_plies(sans, evals, CUR["fm_min"], CUR["fm_max"], CUR["max_abs_eval"], a.loss_max)
                    gs = good_plies(sans, evals, CUR["fm_min"], CUR["fm_max"], CUR["max_abs_eval"], STRICT_LOSS)
                    parsed = (sans, evals, g, gs)
                sans, evals, g, gs = parsed
                if gs:
                    S["games_strict"][k] += 1
                S["pos_strict"][k] += len(gs)
                if not g:
                    continue
                S["reach"][k][len(STAGES)] += 1
                S["pos_good"][k] += len(g)
                S["seen_pool"][k] += 1
                site = h.get(b"Site", "")
                item = dict(game_id=site, white_elo=w, black_elo=b, time_control=h.get(b"TimeControl", ""),
                            result=h.get(b"Result", ""), sans=sans, good=g,
                            white=h.get(b"White", "?").lower(), black=h.get(b"Black", "?").lower())
                res = S["reservoirs"][k]
                if len(res) < RESERVOIR:
                    res.append(item)
                else:
                    j = rngs[k].randrange(S["seen_pool"][k])
                    if j < RESERVOIR:
                        res[j] = item
                if k == ELITE[0]:
                    key = (min(w, b), max(w, b), site)
                    payload = (w, b, h.get(b"TimeControl", ""), len(g))
                    if len(top_heap) < TOP_K:
                        heapq.heappush(top_heap, key); top_payload[key] = payload
                    elif key > top_heap[0]:
                        old = heapq.heapreplace(top_heap, key); top_payload.pop(old, None); top_payload[key] = payload

            now = time.time()
            if S["read"] % 20_000 == 0 and now - last_print >= a.progress_seconds:
                last_print = now
                live_s = max(1e-9, now - t_live); rate = (S["read"] - read_live0) / live_s
                if total_bytes and not a.max_games:
                    frac = hr.n / total_bytes
                    bps = max(1e-9, (hr.n - n_live0) / live_s)
                    eta = (total_bytes - hr.n) / bps
                    where = f"{100*frac:5.1f}% of file ({hr.n/1e9:.2f}/{total_bytes/1e9:.2f} GB)  ETA {fmt_time(eta)}"
                elif a.max_games:
                    eta = (a.max_games - S["read"]) / max(rate, 1e-9)
                    where = f"{100*S['read']/a.max_games:5.1f}% of {a.max_games:,}  ETA {fmt_time(eta)}"
                else:
                    where = f"{hr.n/1e9:.2f} GB read"
                log(f"  {S['read']:>12,} games | {rate:,.0f}/s | {where} | games with %eval {S['evals_seen']:,} | "
                    f"good-move games " + " ".join(f"{k}={S['seen_pool'][k]:,}" for k in ELITE))
                if S["read"] >= 200_000 and S["evals_seen"] == 0:
                    log("  WARNING: no [%eval] annotations seen in 200,000 games - the move-quality check cannot work on this file")
            if S["read"] - last_ckpt >= a.checkpoint_every:
                last_ckpt = S["read"]; checkpoint("periodic")
            if a.stop_after and S["read"] >= a.stop_after:
                raise KeyboardInterrupt
            if a.max_games and S["read"] >= a.max_games:
                break
    except KeyboardInterrupt:
        interrupted = True
        checkpoint("stopped by user")
        log("  STOPPED. Run the SAME command again to continue from this point.")
        sys.exit(0)

    el = time.time() - t_live
    log(f"  finished streaming: {S['read']:,} games in {fmt_time(el)} (this session)")
    log("")

    # ---------------- summary -------------------------------------------------------------------------------
    log("STEP 4/6  Summary tables")
    reach, seen_pool = S["reach"], S["seen_pool"]
    res_out = {"file": name, "games_read": S["read"], "partial": bool(a.max_games), "games_with_eval_annotation": S["evals_seen"],
               "good_move_definition": {"cp_loss_max": a.loss_max, "strict_cp_loss_max": STRICT_LOSS,
                                        "max_abs_eval_before_cp": CUR["max_abs_eval"], "fullmove_window": [CUR["fm_min"], CUR["fm_max"]],
                                        "source": "Lichess server [%eval] annotations, mover's point of view; pre-filter only"},
               "tiers": {}}
    hdr = f"{'tier':<4}{'description':<62}{'games':>10}{'good-move games':>17}{'(<=%dcp)'%STRICT_LOSS:>10}{'good pairs':>12}"
    log(hdr); log("-" * len(hdr))
    for k, t in TIERS.items():
        funnel = dict(zip(["read"] + STAGES, reach[k]))
        d = {**t, "funnel": funnel, "games_all_header_criteria": reach[k][5] if not t["need_eval"] else reach[k][6]}
        if k in ELITE:
            d.update(good_move_games=seen_pool[k], good_move_games_strict=S["games_strict"][k],
                     good_pairs=S["pos_good"][k], good_pairs_strict=S["pos_strict"][k])
            log(f"{k:<4}{t['label']:<62}{d['games_all_header_criteria']:>10,}{seen_pool[k]:>17,}{S['games_strict'][k]:>10,}{S['pos_good'][k]:>12,}")
        else:
            log(f"{k:<4}{t['label']:<62}{d['games_all_header_criteria']:>10,}{'-':>17}{'-':>10}{'-':>12}")
        res_out["tiers"][k] = d
    log("")
    log("  Funnel (games remaining after each criterion):")
    for k in TIERS:
        fn = res_out["tiers"][k]["funnel"]
        names = ["read"] + STAGES[:5] if k == "B0" else ["read"] + STAGES
        log(f"    {k}: " + " -> ".join(f"{s} {fn[s]:,}" for s in names))
    top = sorted(top_heap, reverse=True)
    res_out["highest_rated_games_with_a_good_move"] = [
        {"game_id": s, "min_elo": mn, "white_elo": top_payload[(mn, mx, s)][0], "black_elo": top_payload[(mn, mx, s)][1],
         "time_control": top_payload[(mn, mx, s)][2], "good_pairs": top_payload[(mn, mx, s)][3]} for (mn, mx, s) in top]
    log("")
    log(f"  Highest-rated games with a good position-move pair (top {min(10, len(top))} of {len(top)} saved in the JSON):")
    for r in res_out["highest_rated_games_with_a_good_move"][:10]:
        log(f"    min Elo {r['min_elo']}  ({r['white_elo']} v {r['black_elo']})  {r['time_control']:>8}  good pairs {r['good_pairs']:>3}  {r['game_id']}")
    log("")

    # ---------------- export --------------------------------------------------------------------------------
    log("STEP 5/6  Choosing the pool tier and exporting candidates")
    pool_tier = None
    for k in reversed(ELITE):
        if seen_pool[k] >= a.min_pool:
            pool_tier = k; break
    if pool_tier is None:
        pool_tier = ELITE[0]
        log(f"  WARNING: no elite tier has >= {a.min_pool} good-move games; falling back to {pool_tier} ({seen_pool[pool_tier]:,}). "
            f"Lower --min-pool or read more games.")
    log(f"  pool tier {pool_tier}: {seen_pool[pool_tier]:,} good-move games (reservoir holds {len(S['reservoirs'][pool_tier]):,}); selection = {a.select}")
    pool = S["reservoirs"][pool_tier]
    if a.select == "top":
        pool = sorted(pool, key=lambda g: (-min(g["white_elo"], g["black_elo"]), -max(g["white_elo"], g["black_elo"]), g["game_id"]))
    else:
        pool = sorted(pool, key=lambda g: g["game_id"])
        random.Random(f"{seed}-export").shuffle(pool)
    pick_rng = random.Random(f"{seed}-pick")
    cands, skipped = [], 0
    seen_pos, player_n, skip_dup, skip_player = set(), {}, 0, 0
    for g in pool:
        if len(cands) >= a.n:
            break
        if a.max_per_player and any(player_n.get(x, 0) >= a.max_per_player for x in (g["white"], g["black"])):
            skip_player += 1
            continue
        options = list(g["good"]); pick_rng.shuffle(options)
        done = False
        for (ply, eb, ea, loss) in options:
            board = chess.Board()
            try:
                for s in g["sans"][:ply]:
                    board.push_san(s)
                move = board.parse_san(g["sans"][ply])
            except ValueError:
                break
            if board.is_check() or board.legal_moves.count() < 2:
                continue
            pos_key = " ".join(board.fen().split()[:4])
            if pos_key in seen_pos:
                skip_dup += 1
                continue
            seen_pos.add(pos_key)
            for x in (g["white"], g["black"]):
                player_n[x] = player_n.get(x, 0) + 1
            cands.append({"cand_id": f"C{len(cands)+1:03d}", "game_id": g["game_id"], "white_elo": g["white_elo"],
                          "black_elo": g["black_elo"], "time_control": g["time_control"], "result": g["result"],
                          "ply": ply, "fen_before": board.fen(), "move_uci": move.uci(), "move_san": board.san(move),
                          "side_to_move": "White" if board.turn == chess.WHITE else "Black",
                          "lichess_eval_before_cp": eb, "lichess_eval_after_cp": ea, "lichess_cp_loss": loss, "pool_tier": pool_tier})
            c = cands[-1]
            log(f"  {c['cand_id']}  {c['move_san']:<7} loss {loss:>3} cp  Elo {c['white_elo']}/{c['black_elo']}  ply {ply:<3} {c['game_id']}")
            done = True
            break
        if not done:
            skipped += 1
    log(f"  exported {len(cands)} candidates (games without a usable position: {skipped}; positions skipped as duplicates: {skip_dup}; "
        f"games skipped by the per-player cap: {skip_player}; distinct players in export: {len(player_n)})")
    res_out["export"] = {"pool_tier": pool_tier, "pool_games": seen_pool[pool_tier], "select": a.select, "min_pool": a.min_pool,
                         "n": len(cands), "max_per_player": a.max_per_player,
                         "skipped_duplicate_positions": skip_dup, "skipped_by_player_cap": skip_player,
                         "distinct_players": len(player_n), "candidates": cands}
    log("")

    # ---------------- write ---------------------------------------------------------------------------------
    log("STEP 6/6  Writing files")
    blob = json.dumps(res_out, sort_keys=True).encode()
    pkgs = {}
    for pk in ("chess", "zstandard", "requests"):
        try: pkgs[pk] = metadata.version(pk)
        except metadata.PackageNotFoundError: pass
    manifest = {
        "script_sha256": script_sha, "command_line": " ".join(sys.argv), "input_source": source, "input_name": name,
        "compressed_bytes_consumed": hr.n, "sha256_of_consumed_compressed_bytes": hr.h.hexdigest(),
        "consumed_whole_file": not a.max_games, "max_games": a.max_games, "resumed_from_checkpoint_at_game": resume_from,
        "seed": seed, "loss_max": a.loss_max, "strict_loss": STRICT_LOSS, "max_abs_eval_cp": CUR["max_abs_eval"],
        "fullmove_window": [CUR["fm_min"], CUR["fm_max"]], "max_gap": MAX_GAP, "min_est_seconds_elite": ELITE_MIN_EST,
        "min_plies": CUR["min_plies"], "min_pool": a.min_pool, "n_export": a.n, "select": a.select, "max_per_player": a.max_per_player,
        "tiers": TIERS, "stages": STAGES, "python": sys.version.split()[0], "platform": platform.platform(), "packages": pkgs,
        "seconds_this_session": round(el), "timestamp_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
    final = {"results_sha256": hashlib.sha256(blob).hexdigest(), "manifest": manifest, "results": res_out}
    json.dump(final, open(final_json, "w"), indent=1)
    p_cand = run_dir / "01_candidates.jsonl"
    with open(p_cand, "w", encoding="utf-8") as fh:
        for c in cands:
            fh.write(json.dumps(c) + "\n")
    if ckpt_path.exists():
        ckpt_path.unlink()
    log(f"  {final_json}")
    log(f"  {p_cand}   (sha256 {hashlib.sha256(p_cand.read_bytes()).hexdigest()[:16]}...)")
    log(f"  {LOGFILE}")
    log(f"  results_sha256 = {final['results_sha256']}")
    log(f"  input sha256 of consumed bytes = {hr.h.hexdigest()[:16]}... ({hr.n:,} compressed bytes{', PARTIAL read' if a.max_games else ''})")
    log("")
    log("DONE. Next:  python run_elite_step.py 02   (checks each move with YOUR Stockfish; no copying needed)")


if __name__ == "__main__":
    main()