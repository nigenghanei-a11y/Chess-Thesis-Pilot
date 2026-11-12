"""PROFILE OF THE RAW LICHESS FILE - columns (PGN header tags), fill rates and value distributions.

ONE streaming pass over the WHOLE file (no sampling). Answers: what is in the raw data before any filtering?
  * every header tag ("column") that exists and in how many games it is filled
  * results, terminations, time controls and Lichess speed categories (ultraBullet ... classical)
  * Elo ratings per side (min / mean / median / percentiles) and the Elo distribution of the two players
  * rating gap between the players, game length (full moves), titles (BOT, GM, ...), openings (ECO), games per day
  * how many games carry engine evaluations ([%eval]) and clock times ([%clk]), overall and by rating / time control
    (important: only analysed games can be checked for move quality)
  * data-quality checks: missing/invalid Elo, games without moves, optional duplicate game IDs

Everything goes into   outputs/dataset_profile/<run name>/
    profile_log.txt     everything printed to the screen (with timestamps)
    profile.json        all numbers + reproducibility manifest + results_sha256
    *.csv               one table per topic (open them in Excel for the thesis)

Run (VS Code terminal, project folder, venv active):
  python profile_raw_dataset.py --input data\\raw\\lichess_db_standard_rated_2026-10.pgn.zst
  python profile_raw_dataset.py --input data\\raw\\lichess_db_standard_rated_2026-10.pgn.zst --max-games 500000   # quick test
  add --check-duplicates to also look for repeated game IDs (needs numpy and about 1 GB of RAM)

No resume: if you stop it with Ctrl+C, just run the same command again (the full file takes roughly 30-60 minutes).
Same file + same script  ->  identical results_sha256.
"""
import argparse, csv, hashlib, io, json, platform, re, sys, time
from array import array
from collections import Counter
from importlib import metadata
from pathlib import Path

try:
    import zstandard
except ImportError:
    zstandard = None
try:
    import config as C
    ROOT = C.ROOT
except Exception:
    ROOT = Path(".")

KEEP = {b"Result", b"Termination", b"TimeControl", b"WhiteElo", b"BlackElo", b"WhiteTitle", b"BlackTitle",
        b"Event", b"ECO", b"UTCDate", b"Site"}
LASTNUM = re.compile(rb"(?:^|\s)(\d+)\.(?:\.\.)?(?=\s|$)")
LOGFILE = None


def log(msg=""):
    line = f"[{time.strftime('%H:%M:%S')}] {msg}" if msg else ""
    print(line, flush=True)
    if LOGFILE:
        with open(LOGFILE, "a", encoding="utf-8") as fh:
            fh.write(line + "\n")


def fmt_time(sec):
    sec = int(max(0, sec)); h, r = divmod(sec, 3600); m, s = divmod(r, 60)
    return f"{h}h{m:02d}m" if h else f"{m}m{s:02d}s"


class HashingReader(io.RawIOBase):
    """SHA-256 of exactly the compressed bytes consumed."""
    def __init__(self, raw):
        self.raw, self.h, self.n = raw, hashlib.sha256(), 0
    def readable(self):
        return True
    def readinto(self, b):
        data = self.raw.read(len(b)); n = len(data); b[:n] = data
        self.h.update(data); self.n += n
        return n


def d(b):
    return b.decode("utf-8", "replace") if isinstance(b, (bytes, bytearray)) else str(b)


def tc_category(tc):
    """Lichess speed category from the estimated duration base + 40*increment (seconds)."""
    if not tc or tc == b"-":
        return "correspondence / no clock"
    try:
        base, inc = tc.split(b"+"); sec = int(base) + 40 * int(inc)
    except ValueError:
        return "unparsed time control"
    if sec <= 29: return "ultraBullet"
    if sec <= 179: return "bullet"
    if sec <= 479: return "blitz"
    if sec <= 1499: return "rapid"
    return "classical"


def full_moves(mv):
    """Approximate game length in full moves = last move number near the end of the movetext line."""
    last = None
    for last in LASTNUM.finditer(mv[-400:]):
        pass
    return int(last.group(1)) if last else 0


def elo_stats(c):
    n = sum(c.values())
    if not n:
        return {"n": 0}
    keys, cum, ti, out = sorted(c), 0, 0, {}
    targets = [(0.01, "p1"), (0.05, "p5"), (0.25, "p25"), (0.5, "median"), (0.75, "p75"), (0.95, "p95"), (0.99, "p99")]
    for k in keys:
        cum += c[k]
        while ti < len(targets) and cum >= targets[ti][0] * n:
            out[targets[ti][1]] = k; ti += 1
    return {"n": n, "min": keys[0], "max": keys[-1], "mean": round(sum(k * v for k, v in c.items()) / n, 1), **out}


def pct(x, tot):
    return round(100.0 * x / tot, 3) if tot else 0.0


def iter_games(f, tagc):
    """Yield (header_dict, movetext_bytes); counts every header tag name seen."""
    hdr = {}
    for line in f:
        if line.startswith(b"["):
            sp = line.find(b" ")
            tag = line[1:sp]
            tagc[tag] += 1
            if tag in KEEP:
                hdr[tag] = line[sp + 2: line.rfind(b'"')]
        elif line.strip():
            yield hdr, line
            hdr = {}


def write_csv(path, header, rows):
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh); w.writerow(header); w.writerows(rows)


def main():
    global LOGFILE
    ap = argparse.ArgumentParser()
    ap.add_argument("--input", type=Path, default=ROOT / "data" / "raw" / "lichess_db_standard_rated_2026-10.pgn.zst")
    ap.add_argument("--max-games", type=int, default=0, help="0 = whole file")
    ap.add_argument("--out-dir", type=Path, default=None, help="base folder (default: <project>/outputs/dataset_profile)")
    ap.add_argument("--run-name", default=None)
    ap.add_argument("--check-duplicates", action="store_true", help="look for repeated game IDs (numpy + ~1 GB RAM)")
    ap.add_argument("--progress-seconds", type=int, default=20)
    a = ap.parse_args()
    if not a.input.exists():
        sys.exit(f"ERROR: file not found: {a.input}")

    m = re.search(r"(\d{4}-\d{2})", a.input.name)
    run_name = a.run_name or f"{m.group(1) if m else a.input.stem}_{('first%d' % a.max_games) if a.max_games else 'full'}"
    run_dir = (a.out_dir if a.out_dir else ROOT / "outputs" / "dataset_profile") / run_name
    run_dir.mkdir(parents=True, exist_ok=True)
    LOGFILE = run_dir / "profile_log.txt"
    script_sha = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()

    log("=" * 78)
    log(f"STEP 1/4  Settings   (run folder: {run_dir})")
    log(f"  input        : {a.input}  ({a.input.stat().st_size/1e9:.2f} GB on disk)")
    log(f"  games to read: {'first %s' % format(a.max_games, ',') if a.max_games else 'whole file'}")
    log(f"  duplicate-ID check: {'ON' if a.check_duplicates else 'off'}")

    log("")
    log("STEP 2/4  Opening the input")
    hr = HashingReader(open(a.input, "rb"))
    if a.input.suffix == ".zst":
        if zstandard is None:
            sys.exit("ERROR: pip install zstandard")
        f = io.BufferedReader(zstandard.ZstdDecompressor(max_window_size=2**31).stream_reader(hr), buffer_size=1 << 22)
    else:
        f = io.BufferedReader(hr, buffer_size=1 << 22)
    total_bytes = a.input.stat().st_size

    tagc, res_c, term_c, tc_c, title_c, event_c, eco_c, day_c = (Counter() for _ in range(8))
    elo_w, elo_b = Counter(), Counter()
    cat_g, cat_e = Counter(), Counter()
    bin_g, bin_e = Counter(), Counter()
    gap_c, len_c = Counter(), Counter()
    n = n_eval = n_clk = n_bot = n_title = n_noelo = n_bothelo = n_empty = 0
    ids = array("Q") if a.check_duplicates else None
    t0 = time.time(); last_print = t0

    log("")
    log("STEP 3/4  Streaming games (progress line every %ds; Ctrl+C stops without writing results)" % a.progress_seconds)
    try:
        for h, mv in iter_games(f, tagc):
            n += 1
            has_eval = b"[%eval" in mv
            has_clk = b"[%clk" in mv
            n_eval += has_eval; n_clk += has_clk
            res_c[d(h.get(b"Result", b"<missing>"))] += 1
            term_c[d(h.get(b"Termination", b"<missing>"))] += 1
            event_c[d(h.get(b"Event", b"<missing>"))] += 1
            if b"UTCDate" in h: day_c[d(h[b"UTCDate"])] += 1
            if b"ECO" in h: eco_c[d(h[b"ECO"])] += 1
            tc = h.get(b"TimeControl")
            tc_c[d(tc) if tc is not None else "<missing>"] += 1
            cat = tc_category(tc); cat_g[cat] += 1; cat_e[cat] += has_eval
            wt, bt = h.get(b"WhiteTitle"), h.get(b"BlackTitle")
            for t in (wt, bt):
                if t: title_c[d(t)] += 1
            if wt == b"BOT" or bt == b"BOT": n_bot += 1
            if wt or bt: n_title += 1
            try:
                w = int(h[b"WhiteElo"]); bl = int(h[b"BlackElo"])
            except (KeyError, ValueError):
                n_noelo += 1; w = bl = None
            if w is not None:
                n_bothelo += 1
                elo_w[w] += 1; elo_b[bl] += 1
                ab = (w + bl) // 2 // 100 * 100
                bin_g[ab] += 1; bin_e[ab] += has_eval
                gap_c[min(abs(w - bl) // 50, 10) * 50] += 1
            fm = full_moves(mv)
            if fm == 0: n_empty += 1
            len_c[min(fm // 10, 10) * 10] += 1
            if ids is not None:
                s = h.get(b"Site", b"")
                if len(s) >= 8: ids.append(int.from_bytes(s[-8:], "big"))
            now = time.time()
            if n % 20_000 == 0 and now - last_print >= a.progress_seconds:
                last_print = now
                rate = n / max(1e-9, now - t0)
                if a.max_games:
                    where = f"{100*n/a.max_games:5.1f}% of {a.max_games:,}  ETA {fmt_time((a.max_games-n)/max(rate,1e-9))}"
                else:
                    bps = hr.n / max(1e-9, now - t0)
                    where = f"{100*hr.n/total_bytes:5.1f}% of file ({hr.n/1e9:.2f}/{total_bytes/1e9:.2f} GB)  ETA {fmt_time((total_bytes-hr.n)/max(bps,1e-9))}"
                log(f"  {n:>12,} games | {rate:,.0f}/s | {where} | with %eval {pct(n_eval, n):.1f}% | both Elo valid {pct(n_bothelo, n):.1f}% | BOT games {n_bot:,}")
            if a.max_games and n >= a.max_games:
                break
    except KeyboardInterrupt:
        log("  STOPPED by user - nothing written. Run the same command again to redo the profile.")
        sys.exit(0)
    el = time.time() - t0
    log(f"  finished: {n:,} games in {fmt_time(el)}")

    log("")
    log("STEP 4/4  Summary and files")
    dup = None
    if ids is not None:
        try:
            import numpy as np
            arr = np.sort(np.frombuffer(ids, dtype=np.uint64))
            dup = int((arr[1:] == arr[:-1]).sum())
            log(f"  duplicate game IDs: {dup:,} (of {len(arr):,} IDs checked)")
        except ImportError:
            log("  duplicate check skipped: pip install numpy")

    results = {
        "file": a.input.name, "games_read": n, "partial": bool(a.max_games),
        "header_tags": {d(k): {"games_with_tag": v, "pct": pct(v, n)} for k, v in sorted(tagc.items(), key=lambda kv: -kv[1])},
        "engine_eval_annotations": {"games_with_eval": n_eval, "pct": pct(n_eval, n), "games_with_clock": n_clk, "pct_clock": pct(n_clk, n)},
        "quality": {"games_missing_or_invalid_elo": n_noelo, "games_without_moves": n_empty, "games_with_bot_account": n_bot,
                    "games_with_any_title": n_title, "duplicate_game_ids": dup},
        "elo_white": elo_stats(elo_w), "elo_black": elo_stats(elo_b),
        "results": dict(res_c.most_common()), "terminations": dict(term_c.most_common()),
        "speed_categories": {k: {"games": cat_g[k], "pct": pct(cat_g[k], n), "with_eval": cat_e[k], "pct_with_eval": pct(cat_e[k], cat_g[k])}
                             for k, _ in cat_g.most_common()},
        "titles": dict(title_c.most_common()),
        "note": "game length = last move number near the end of the movetext (approximate); Elo bins use the average of both players",
    }
    blob = json.dumps(results, sort_keys=True).encode()
    pk = {}
    for p in ("zstandard", "numpy"):
        try: pk[p] = metadata.version(p)
        except metadata.PackageNotFoundError: pass
    manifest = {"script_sha256": script_sha, "command_line": " ".join(sys.argv), "input_name": a.input.name,
                "compressed_bytes_consumed": hr.n, "sha256_of_consumed_compressed_bytes": hr.h.hexdigest(),
                "consumed_whole_file": not a.max_games, "python": sys.version.split()[0], "platform": platform.platform(),
                "packages": pk, "seconds": round(el), "timestamp_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
    final = {"results_sha256": hashlib.sha256(blob).hexdigest(), "manifest": manifest, "results": results}
    with open(run_dir / "profile.json", "w", encoding="utf-8") as fh:
        json.dump(final, fh, indent=1)

    write_csv(run_dir / "tags.csv", ["header_tag", "games_with_tag", "pct_of_games"],
              [(d(k), v, pct(v, n)) for k, v in tagc.most_common()])
    write_csv(run_dir / "speed_categories.csv", ["category", "games", "pct", "games_with_eval", "pct_with_eval"],
              [(k, cat_g[k], pct(cat_g[k], n), cat_e[k], pct(cat_e[k], cat_g[k])) for k, _ in cat_g.most_common()])
    write_csv(run_dir / "time_controls_top50.csv", ["time_control", "games", "pct"], [(k, v, pct(v, n)) for k, v in tc_c.most_common(50)])
    write_csv(run_dir / "elo_bins_avg_of_players.csv", ["avg_elo_bin_from", "games", "pct", "games_with_eval", "pct_with_eval"],
              [(k, bin_g[k], pct(bin_g[k], n), bin_e[k], pct(bin_e[k], bin_g[k])) for k in sorted(bin_g)])
    write_csv(run_dir / "elo_gap_bins.csv", ["gap_from (50 = 50-99, 500 = 500+)", "games", "pct"], [(k, gap_c[k], pct(gap_c[k], n)) for k in sorted(gap_c)])
    write_csv(run_dir / "game_length_bins.csv", ["full_moves_from (100 = 100+)", "games", "pct"], [(k, len_c[k], pct(len_c[k], n)) for k in sorted(len_c)])
    for name, c in (("results", res_c), ("terminations", term_c), ("titles", title_c), ("events", event_c),
                    ("eco_top100", Counter(dict(eco_c.most_common(100)))), ("games_per_day", day_c)):
        rows = sorted(c.items()) if name == "games_per_day" else c.most_common()
        write_csv(run_dir / f"{name}.csv", ["value", "games", "pct"], [(k, v, pct(v, n)) for k, v in rows])

    log("")
    log("  HEADLINE KPIs")
    log(f"    games read                    : {n:,}")
    log(f"    header tags found             : {len(tagc)}  ({', '.join(d(k) for k, _ in tagc.most_common())})")
    log(f"    games with engine eval        : {n_eval:,} ({pct(n_eval, n):.2f}%)   with clock times: {n_clk:,} ({pct(n_clk, n):.2f}%)")
    log(f"    games with a BOT account      : {n_bot:,} ({pct(n_bot, n):.2f}%)   with any title: {n_title:,}")
    log(f"    missing/invalid Elo           : {n_noelo:,}    games without moves: {n_empty:,}")
    for side, c in (("White", elo_w), ("Black", elo_b)):
        s = elo_stats(c)
        if s["n"]: log(f"    {side} Elo  min {s['min']}  p5 {s.get('p5')}  median {s.get('median')}  mean {s['mean']}  p95 {s.get('p95')}  p99 {s.get('p99')}  max {s['max']}")
    log("    speed categories              : " + "; ".join(f"{k} {pct(v, n):.1f}%" for k, v in cat_g.most_common()))
    log("    results                       : " + "; ".join(f"{k} {pct(v, n):.1f}%" for k, v in res_c.most_common()))
    log("    terminations                  : " + "; ".join(f"{k} {pct(v, n):.1f}%" for k, v in term_c.most_common()))
    log("")
    log(f"  results_sha256 = {final['results_sha256']}")
    log(f"  input sha256 of consumed bytes = {hr.h.hexdigest()[:16]}... ({hr.n:,} compressed bytes{', PARTIAL read' if a.max_games else ''})")
    log(f"  files in {run_dir}: profile.json, profile_log.txt and the CSV tables")
    log("DONE.")


if __name__ == "__main__":
    main()
