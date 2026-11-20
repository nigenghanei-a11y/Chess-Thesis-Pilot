"""Pre-flight check for the thesis pipeline: do the environment and all project files agree?

Run from the project folder, inside the activated .venv:
    python check_setup.py            # offline checks
    python check_setup.py --online   # also asks the Lichess server for the size of the pinned file

Prints [ OK ] / [INFO] / [WARN] / [FAIL] lines. Exit code 0 = no FAIL (warnings are allowed), 1 = at least one FAIL.
Only the standard library is needed to start it; the packages themselves are checked inside.
"""
import argparse, importlib, importlib.metadata as md, io, json, os, re, shutil, subprocess, sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
EXPECTED_PYTHON = (3, 11, 9)                      # README: Python 3.11.9
MIN_FREE_GB = 40                                  # ~31 GB raw file + ~1 GB working files + margin
FILES = ["config.py", "README.md", "requirements.txt", "requirements-lock.txt",
         "step0_download_and_profile.ipynb", "step1_select_positions.ipynb",
         "check_setup.py", ".gitignore"]
REQUIRED_GITIGNORE = [".venv/", "data/raw/", "outputs/**/checkpoint.pkl", "outputs/**/profile_state.pkl",
                      "outputs/**/game_ids.u64", "outputs/step1_dataset/**/candidate_games.pgn",
                      "outputs/stockfish_recheck/**/resume_state.json*", "stockfish/"]
IGNORED_EXTRAS = {"pip", "setuptools", "wheel", "distribute"}      # never listed by `pip freeze`
IMPORTS = ["numpy", "pandas", "matplotlib", "requests", "zstandard", "chess", "chess.pgn", "scipy", "statsmodels", "ipykernel"]
CONFIG_NAMES = ["MONTH", "BASE_URL", "LICHESS_FILE", "ELO_MIN", "MAX_RATING_GAP", "MIN_EST_SECONDS", "MIN_PLIES",
                "STOCKFISH_VERSION", "STOCKFISH_DEPTH", "STOCKFISH_THREADS", "STOCKFISH_HASH_MB", "STOCKFISH_MULTIPV",
                "STOCKFISH_PATH", "PV_MAX_PLIES", "SEED_SELECTION", "SEED_PILOT_SPLIT", "SEED_ANNOTATION",
                "SEED_BOOTSTRAP", "SEED_LLM", "ELO_PERCENTILE", "GAMES_TARGET", "BAD_DEFINITION",
                "BAD_ONLY_IN_GAMES_WITH_GOOD", "FULLMOVE_MIN", "FULLMOVE_MAX", "MAX_ABS_EVAL_CP",
                "GOOD_MAX_CP_LOSS", "BAD_MIN_CP_LOSS", "BAD_MIN_WINPROB_DROP", "POSITIONS_PER_GAME",
                "PILOT_N", "PILOT_EXCLUDED_FROM_FINAL"]

results = []                                       # (level, message)


def rep(level, msg):
    results.append((level, msg)); print(f"[{level:^4}] {msg}")


def norm(name):
    return re.sub(r"[-_.]+", "-", name).lower()


def parse_pins(text):
    """name==version lines of a requirements file -> {normalised name: version}; comments and other lines are ignored."""
    pins = {}
    for line in text.splitlines():
        m = re.match(r"^\s*([A-Za-z0-9][A-Za-z0-9_.\-]*)\s*==\s*([^\s#;]+)", line)
        if m: pins[norm(m.group(1))] = m.group(2)
    return pins


def get_installed():
    out = {}
    for d in md.distributions():
        name = d.metadata["Name"]
        if name: out[norm(name)] = d.version
    return out


# ---------------------------------------------------------------- individual checks
def check_python():
    v = sys.version_info[:3]
    if v == EXPECTED_PYTHON: rep("OK", f"Python {'.'.join(map(str, v))}")
    elif v[:2] == EXPECTED_PYTHON[:2]: rep("WARN", f"Python {'.'.join(map(str, v))}; the README and manifests say {'.'.join(map(str, EXPECTED_PYTHON))}")
    else: rep("FAIL", f"Python {'.'.join(map(str, v))}; this project needs {'.'.join(map(str, EXPECTED_PYTHON))}")


def check_venv():
    in_venv = sys.prefix != sys.base_prefix
    want = os.path.normcase(str((ROOT / ".venv").resolve())); have = os.path.normcase(str(Path(sys.prefix).resolve()))
    if in_venv and have == want: rep("OK", f"running inside the project's .venv ({sys.executable})")
    elif in_venv: rep("FAIL", f"a venv is active but it is not this project's .venv: {sys.prefix}")
    else: rep("FAIL", f"NOT inside a venv: {sys.executable}. Activate .venv (.\\.venv\\Scripts\\Activate.ps1) and run `python check_setup.py` again")
    if Path.cwd().resolve() != ROOT: rep("WARN", f"current folder is {Path.cwd()}, not the project folder {ROOT} (the notebook uses the current folder)")


def check_files():
    missing = [f for f in FILES if not (ROOT / f).exists()]
    if missing: rep("FAIL", "missing files: " + ", ".join(missing) + (" (is .gitignore saved with the leading dot?)" if ".gitignore" in missing else ""))
    else: rep("OK", "all project files are present: " + ", ".join(FILES))


def check_packages(req, lock, installed):
    """requirements.txt vs requirements-lock.txt vs what is installed in this environment."""
    bad = [f"{n}: requirements.txt {v}, lock {lock.get(n, 'MISSING')}" for n, v in req.items() if lock.get(n) != v]
    rep("FAIL" if bad else "OK", ("requirements.txt and requirements-lock.txt disagree: " + "; ".join(bad)) if bad else f"requirements.txt ({len(req)} packages) agrees with requirements-lock.txt")
    bad = [f"{n}: want {v}, installed {installed.get(n, 'MISSING')}" for n, v in req.items() if installed.get(n) != v]
    rep("FAIL" if bad else "OK", ("direct dependencies differ from requirements.txt: " + "; ".join(bad)) if bad else f"all {len(req)} direct dependencies installed at the pinned versions")
    bad = [f"{n}: lock {v}, installed {installed.get(n, 'MISSING')}" for n, v in lock.items() if installed.get(n) != v]
    rep("FAIL" if bad else "OK", (f"{len(bad)} of {len(lock)} packages differ from requirements-lock.txt: " + "; ".join(bad[:12]) + (" ..." if len(bad) > 12 else "")
        + "  -> fix: python -m pip install -r requirements-lock.txt") if bad else f"all {len(lock)} packages equal requirements-lock.txt")
    extra = sorted(n for n in installed if n not in lock and n not in IGNORED_EXTRAS)
    if extra: rep("WARN", "installed but not in requirements-lock.txt: " + ", ".join(extra))


def check_imports_and_smoke():
    for mod in IMPORTS:
        try: importlib.import_module(mod)
        except Exception as e: rep("FAIL", f"import {mod} failed: {type(e).__name__}: {e}"); return
    rep("OK", "all packages import: " + ", ".join(IMPORTS))
    try:                                                      # same reader construction as the notebook
        import zstandard
        data = b'[Event "x"]\n\n1. e4 e5\n'
        comp = zstandard.ZstdCompressor().compress(data)
        r = io.BufferedReader(zstandard.ZstdDecompressor(max_window_size=2**31).stream_reader(io.BytesIO(comp)), buffer_size=1 << 22)
        assert r.read() == data
        rep("OK", "zstandard: compress -> stream-read round trip works (same reader settings as step 0)")
    except Exception as e: rep("FAIL", f"zstandard smoke test failed: {type(e).__name__}: {e}")
    try:
        import chess.pgn
        g = chess.pgn.read_game(io.StringIO("1. e4 e5 2. Nf3 *"))
        assert g is not None and not g.errors and sum(1 for _ in g.mainline_moves()) == 3
        rep("OK", "python-chess: PGN replay works")
    except Exception as e: rep("FAIL", f"python-chess smoke test failed: {type(e).__name__}: {e}")
    try:
        import pandas as pd
        assert pd.DataFrame({"a": [1.5]}).to_csv(index=False, lineterminator="\n", float_format="%.10g") == "a\n1.5\n"
        rep("OK", "pandas: CSV writer gives the fixed line endings / number format the results hash relies on")
    except Exception as e: rep("FAIL", f"pandas CSV smoke test failed: {type(e).__name__}: {e}")
    try:
        p = subprocess.run([sys.executable, "-m", "pip", "check"], capture_output=True, text=True, timeout=120)
        if p.returncode == 0: rep("OK", "pip check: no broken or conflicting dependencies")
        else: rep("FAIL", "pip check found problems: " + " | ".join((p.stdout + p.stderr).strip().splitlines()[:6]))
    except Exception as e: rep("WARN", f"could not run pip check: {type(e).__name__}: {e}")


def check_config_and_notebook():
    sys.path.insert(0, str(ROOT))
    try:
        import config; importlib.reload(config)
    except Exception as e: rep("FAIL", f"config.py does not import: {type(e).__name__}: {e}"); return None
    missing = [n for n in CONFIG_NAMES if not hasattr(config, n)]
    if missing: rep("FAIL", "config.py lacks: " + ", ".join(missing)); return None
    rep("OK", f"config.py imports; month {config.MONTH}, Stockfish {config.STOCKFISH_VERSION} depth {config.STOCKFISH_DEPTH}")
    if not re.fullmatch(r"\d{4}-\d{2}", config.MONTH): rep("FAIL", f"MONTH should look like 2026-10, got {config.MONTH!r}")
    if config.LICHESS_FILE != f"lichess_db_standard_rated_{config.MONTH}.pgn.zst": rep("FAIL", f"LICHESS_FILE {config.LICHESS_FILE!r} does not follow MONTH")
    if not config.BASE_URL.endswith("/"): rep("FAIL", "BASE_URL must end with '/'")
    if not 0 < config.ELO_PERCENTILE <= 100: rep("FAIL", f"ELO_PERCENTILE must be in (0, 100], got {config.ELO_PERCENTILE!r}")
    if config.BAD_DEFINITION not in {"cp", "winprob"}: rep("FAIL", f"BAD_DEFINITION must be 'cp' or 'winprob', got {config.BAD_DEFINITION!r}")
    if config.GAMES_TARGET is not None and config.GAMES_TARGET < 1: rep("FAIL", f"GAMES_TARGET must be positive or None, got {config.GAMES_TARGET!r}")
    if config.PILOT_N < 0: rep("FAIL", f"PILOT_N must not be negative, got {config.PILOT_N!r}")
    if config.POSITIONS_PER_GAME != {"good": 1, "bad": 1}: rep("FAIL", "Step 1 requires POSITIONS_PER_GAME = {'good': 1, 'bad': 1}")
    seeds = [getattr(config, n) for n in CONFIG_NAMES if n.startswith("SEED_")]
    rep("OK" if len(set(seeds)) == len(seeds) else "FAIL", "seeds are all different (one per purpose)" if len(set(seeds)) == len(seeds) else f"seeds are reused: {seeds}")
    try: json.dumps(config.config_snapshot()); rep("OK", "config_snapshot() can be written to a manifest")
    except Exception as e: rep("FAIL", f"config_snapshot() is not JSON-serialisable: {e}")
    try:
        nb = json.loads((ROOT / "step0_download_and_profile.ipynb").read_text(encoding="utf-8"))
        code = "\n".join("".join(c["source"]) for c in nb["cells"] if c["cell_type"] == "code")
    except Exception as e: rep("FAIL", f"cannot read the notebook: {type(e).__name__}: {e}"); return config
    if "MIN_EST_SECONDS" in code and "MAX_RATING_GAP" in code and not re.search(r"int\(gap\s*<=\s*\d", code) and not re.search(r"est\s*>=\s*\d", code):
        rep("OK", "notebook takes the rating-gap and minimum-time thresholds from config.py (no hard-coded copies)")
    else: rep("FAIL", "notebook still hard-codes the rating-gap / minimum-time threshold instead of reading it from config.py")
    m = re.search(r"THRESH\s*=\s*\[([^\]]*)\]", code)
    tiers_ok = bool(m and ("config.ELO_MIN" in m.group(1) or config.ELO_MIN in [int(x) for x in re.findall(r"\d+", m.group(1))]))
    tiers = [int(x) for x in re.findall(r"\d+", m.group(1))] if m else []
    rep("OK" if tiers_ok else "FAIL", f"ELO_MIN {config.ELO_MIN} is one of the notebook's rating tiers" if tiers_ok else f"ELO_MIN {config.ELO_MIN} is not in the notebook's THRESH {tiers}")
    for manifest_name in ("download_manifest.json", "profile_manifest.json"):
        manifest_path = ROOT / "outputs" / "step0_dataset" / config.MONTH / manifest_name
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            missing = [key for key in ("config", "git_commit") if key not in manifest]
            rep("FAIL" if missing else "OK", f"{manifest_name} has config and git provenance" if not missing else f"{manifest_name} lacks provenance fields: {', '.join(missing)}")
        except Exception as e:
            rep("FAIL", f"cannot verify provenance in {manifest_name}: {type(e).__name__}: {e}")
    try:
        step1_notebook = json.loads((ROOT / "step1_select_positions.ipynb").read_text(encoding="utf-8"))
        readme = (ROOT / "README.md").read_text(encoding="utf-8")
        step1_code_cells = ["".join(c["source"]) for c in step1_notebook["cells"]
                            if c["cell_type"] == "code" and "step1" in c.get("metadata", {}).get("tags", [])]
        step2_code_cells = ["".join(c["source"]) for c in step1_notebook["cells"]
                            if c["cell_type"] == "code" and "step2" in c.get("metadata", {}).get("tags", [])]
        if not step1_code_cells or not step2_code_cells:
            rep("FAIL", "Step 1 notebook must include executable Step 1 and Step 2 cells")
        else:
            for index, code in enumerate(step1_code_cells + step2_code_cells, 1):
                compile(code, f"step1_select_positions.ipynb:cell-{index}", "exec")
            rep("OK", "Step 1 notebook includes syntactically valid Step 1 and Step 2 code cells")
        step1_code = "\n".join(step1_code_cells)
        step2_code = "\n".join(step2_code_cells)
        if "config.ELO_PERCENTILE" in step1_code and "outputs/step1_dataset/<month>/" in readme and "step1_dataset" in step1_code:
            rep("OK", "Step 1 uses the configured rating percentile and the README documents its output folder")
        else:
            rep("FAIL", "Step 1 percentile settings or output-folder documentation disagree")
        if ("positions_final.csv" in step2_code and "STOCKFISH_DEPTH" in step2_code
                and "RUN_STOCKFISH =" in step2_code and "if RUN_STOCKFISH:" in step2_code
                and "pilot_split_complete" in step2_code
                and "Complete the pilot split and regenerate Step 1 outputs" not in step2_code
                and "MAKE_PILOT_SPLIT = False" in step1_code and "Step 2 - Stockfish re-check" in readme):
            rep("OK", "Step 2 is gated by RUN_STOCKFISH and records pilot status without requiring a split")
        else:
            rep("FAIL", "Step 2 notebook code or documentation does not match its configured input/settings")
    except Exception as e:
        rep("FAIL", f"cannot verify Step 1/2 notebook: {type(e).__name__}: {e}")
    return config


def check_gitignore():
    try: lines = {l.strip() for l in (ROOT / ".gitignore").read_text(encoding="utf-8").splitlines()}
    except Exception as e: rep("FAIL", f"cannot read .gitignore: {e}"); return
    miss = [p for p in REQUIRED_GITIGNORE if p not in lines]
    rep("FAIL" if miss else "OK", ("missing from .gitignore: " + ", ".join(miss)) if miss else "the .gitignore covers the venv, the raw data, the working files and the Stockfish binary")


def check_disk_and_raw(config, online):
    raw = config.RAW_DIR / config.LICHESS_FILE; part = raw.with_name(raw.name + ".part")
    probe = config.RAW_DIR
    while not probe.exists() and probe != probe.parent: probe = probe.parent
    free_gb = shutil.disk_usage(probe).free / 1e9
    remote = None
    if online:
        try:
            import urllib.request
            req = urllib.request.Request(config.BASE_URL + config.LICHESS_FILE, method="HEAD")
            with urllib.request.urlopen(req, timeout=30) as r: remote = int(r.headers["Content-Length"])
            rep("INFO", f"the server announces {remote:,} bytes ({remote / 1e9:.2f} GB) for {config.LICHESS_FILE}")
        except Exception as e: rep("WARN", f"could not reach the Lichess server: {type(e).__name__}: {e}")
    if raw.exists():
        size = raw.stat().st_size
        ok = remote is None or size == remote
        rep("OK" if ok else "FAIL", f"raw file present: {size:,} bytes" + ("" if remote is None else (" = server size" if ok else f" but the server has {remote:,} bytes")))
    elif part.exists(): rep("INFO", f"partial download present ({part.stat().st_size / 1e9:.2f} GB): run the download cell again to resume")
    else:
        need = MIN_FREE_GB if remote is None else remote / 1e9 + 9
        rep("OK" if free_gb >= need else "WARN", f"free space on the data drive: {free_gb:.0f} GB (the download needs about {need:.0f} GB incl. working files)")


def main():
    ap = argparse.ArgumentParser(); ap.add_argument("--online", action="store_true", help="also ask the Lichess server for the file size")
    args = ap.parse_args()
    print(f"project folder: {ROOT}\n")
    check_python(); check_venv(); check_files()
    try:
        req, lock = parse_pins((ROOT / "requirements.txt").read_text(encoding="utf-8")), parse_pins((ROOT / "requirements-lock.txt").read_text(encoding="utf-8"))
        check_packages(req, lock, get_installed())
    except FileNotFoundError as e: rep("FAIL", f"cannot read a requirements file: {e}")
    check_imports_and_smoke()
    config = check_config_and_notebook(); check_gitignore()
    if config is not None: check_disk_and_raw(config, args.online)
    n = {k: sum(1 for l, _ in results if l == k) for k in ("OK", "INFO", "WARN", "FAIL")}
    print(f"\n{n['OK']} ok, {n['INFO']} info, {n['WARN']} warnings, {n['FAIL']} failures")
    print("ALL CHECKS PASSED" if n["FAIL"] == 0 else "SOME CHECKS FAILED - fix the [FAIL] lines above, then run this script again")
    sys.exit(1 if n["FAIL"] else 0)


if __name__ == "__main__":
    main()
