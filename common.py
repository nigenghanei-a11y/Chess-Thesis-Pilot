"""Shared helpers: hashing + run manifest (what makes a run reproducible/auditable)."""
import hashlib, json, platform, sys, datetime
from importlib import metadata
import config


def sha256_file(path, chunk=1 << 20):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while block := f.read(chunk):
            h.update(block)
    return h.hexdigest()


def config_snapshot():
    return {k: str(v) for k, v in vars(config).items() if k.isupper()}


def write_manifest(path, step, extra=None):
    pkgs = {}
    for p in ["chess", "zstandard", "requests", "pandas", "scipy", "statsmodels", "ollama"]:
        try:
            pkgs[p] = metadata.version(p)
        except metadata.PackageNotFoundError:
            pass
    manifest = {
        "step": step,
        "timestamp_utc": datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds"),
        "python": sys.version.split()[0],
        "platform": platform.platform(),
        "packages": pkgs,
        "config": config_snapshot(),
        **(extra or {}),
    }
    with open(path, "w", encoding="utf-8") as f:
        json.dump(manifest, f, indent=2)
    return manifest
