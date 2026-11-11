"""Run pipeline step 02 (Stockfish check) or 03 (LLM explanations) directly on an elite run folder.

No copying of files: this points config.OUT_DIR at outputs/elite_count/<run>/ for the duration of the step,
so 02 reads <run>/01_candidates.jsonl and writes its results next to it (and 03 reads 02's output there).

Usage (project folder, venv active):
  python run_elite_step.py 02                       # uses the only run folder that has 01_candidates.jsonl
  python run_elite_step.py 02 --run 2026-10_full    # choose a run when there are several
  python run_elite_step.py 03 --run 2026-10_full
"""
import argparse, runpy, sys

import config as C

SCRIPTS = {"02": "02_analyze_filter.py", "03": "03_generate_explanations.py"}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("step", choices=sorted(SCRIPTS))
    ap.add_argument("--run", default=None, help="run folder name inside outputs/elite_count/")
    a = ap.parse_args()

    base = C.ROOT / "outputs" / "elite_count"
    runs = sorted(p.name for p in base.glob("*") if (p / "01_candidates.jsonl").exists()) if base.exists() else []
    if a.run:
        run = a.run
    elif len(runs) == 1:
        run = runs[0]
    else:
        sys.exit("Choose a run with --run NAME. Available runs: " + (", ".join(runs) if runs else "none (run count_eligible_elite.py first)"))
    run_dir = base / run
    if not (run_dir / "01_candidates.jsonl").exists():
        sys.exit(f"No 01_candidates.jsonl in {run_dir}")

    C.OUT_DIR = run_dir
    script = C.ROOT / SCRIPTS[a.step]
    print(f"[run_elite_step] step {a.step} on run folder: {run_dir}", flush=True)
    runpy.run_path(str(script), run_name="__main__")


if __name__ == "__main__":
    main()
