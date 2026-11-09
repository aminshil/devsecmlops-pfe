#!/usr/bin/env python3
"""Local model store: each site keeps its promoted model on its own machine, with no Git push.

A promoted model never leaves the site. The model job writes it here; the application pipeline
bakes it into the image from here. Git only distributes the code.

Layout, under MODEL_STORE (default /var/jenkins_home/model-store, inside the jenkins_home volume):

    <site>/current/                     the files the image bakes in (FILES, the Dockerfile's COPY list)
    <site>/versions/<seq>-<UTC>-<why>/  every earlier 'current', kept for rollback (highest seq = newest)
    <site>/rolled-back/<seq>-<UTC>-.../  a model taken out by a rollback (kept, never restored automatically)
    <site>/log.jsonl                    one line per init / activation / rollback

Commands:
    init     --site S --from DIR             create <site>/current from DIR if the site has none yet
    stage    --site S --to DIR               copy current into a working folder the training compares with
    activate --site S --from DIR --build N   keep current as a version, then DIR becomes current
    export   --site S --to DIR               copy current for a docker build, every hash checked
    rollback --site S                        the newest kept version becomes current again; repeat to go further back
    status   --site S                        what is current, and the versions kept
    exists   --site S                        exit 0 if the site has a current model, 1 if not

Every command that reads or writes a model folder first checks that the six production
artifacts match the SHA-256 recorded in its manifest.json, as the API does before loading.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path

DEFAULT_STORE = "/var/jenkins_home/model-store"

# The files the Dockerfile bakes into the image (tests/test_model_store.py keeps the two lists equal).
FILES = (
    "telecom_serving_model.pkl",
    "telecom_serving_baselines.json",
    "telecom_xgb_classifier_v2.pkl",
    "telecom_xgb_label_encoder_v2.pkl",
    "telecom_xgb_v4_rolling.pkl",
    "telecom_xgb_v4_rolling_encoder.pkl",
    "telecom_iso_v2.pkl",
    "telecom_baselines_v2.json",
    "dependency_graph.json",
    "manifest.json",
)

SITE_RE = re.compile(r"[a-z0-9][a-z0-9_-]{0,31}")


class StoreError(RuntimeError):
    pass


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def site_dir(store: Path, site: str) -> Path:
    if not SITE_RE.fullmatch(site):
        raise StoreError(f"invalid site name {site!r}: lowercase letters, digits, '-' and '_' only (max 32)")
    return store / site


def check_folder(folder: Path) -> dict:
    """Every file present, and the six production artifacts match the manifest. Returns the manifest."""
    if not folder.is_dir():
        raise StoreError(f"model folder not found: {folder}")
    missing = [n for n in FILES if not (folder / n).is_file()]
    if missing:
        raise StoreError(f"{folder}: missing {', '.join(missing)}")
    manifest = json.loads((folder / "manifest.json").read_text())
    arts = (manifest.get("production") or {}).get("artifacts") or {}
    if not arts:
        raise StoreError(f"{folder}: the manifest lists no production artifact")
    bad = [rel for rel, expected in arts.items() if _sha256(folder / Path(rel).name) != expected]
    if bad:
        raise StoreError(f"{folder}: SHA-256 differs from the manifest for {', '.join(bad)}")
    return manifest


def _copy_files(src: Path, dst: Path) -> None:
    dst.mkdir(parents=True, exist_ok=False)
    for n in FILES:
        shutil.copy2(src / n, dst / n)


def _summary(manifest: dict) -> dict:
    prod = manifest.get("production") or {}
    ev = prod.get("evaluation") or {}
    return {"promoted_at": prod.get("promoted_at"), "mlflow_run_id": prod.get("mlflow_run_id"),
            "served_v4_f1": (ev.get("v4") or {}).get("f1"),
            "threshold_v4": prod.get("decision_threshold"), "threshold_v3": prod.get("decision_threshold_v3")}


def _next_slot(parent: Path, suffix: str) -> Path:
    """A new, unique folder name under parent, ordered by a sequence number (never by clock alone)."""
    seqs = [int(p.name.split("-", 1)[0]) for p in parent.glob("*") if p.name.split("-", 1)[0].isdigit()] if parent.exists() else []
    return parent / f"{max(seqs, default=0) + 1:04d}-{_now()}-{suffix}"


def _versions(sdir: Path) -> list[Path]:
    d = sdir / "versions"
    return sorted((p for p in d.glob("*") if p.name.split("-", 1)[0].isdigit()),
                  key=lambda p: int(p.name.split("-", 1)[0])) if d.exists() else []


def _log(sdir: Path, event: str, **fields) -> None:
    with open(sdir / "log.jsonl", "a") as f:
        f.write(json.dumps({"at": _now(), "event": event, **fields}) + "\n")


def _install(sdir: Path, src: Path, keep_as: Path | None) -> Path | None:
    """Copy src next to current, check it, move the old current to keep_as, then switch."""
    incoming = sdir / ".incoming"
    if incoming.exists():
        shutil.rmtree(incoming)
    _copy_files(src, incoming)
    check_folder(incoming)
    current = sdir / "current"
    kept = None
    if current.exists():
        if keep_as is None:
            raise StoreError(f"{sdir.name}: refusing to replace current without keeping it")
        kept = keep_as
        kept.parent.mkdir(parents=True, exist_ok=True)
        current.rename(kept)
    incoming.rename(current)
    return kept


def cmd_init(store: Path, site: str, src: Path) -> str:
    sdir = site_dir(store, site)
    if (sdir / "current").exists():
        check_folder(sdir / "current")
        return f"{site}: already initialised, current kept"
    manifest = check_folder(src)
    sdir.mkdir(parents=True, exist_ok=True)
    _install(sdir, src, None)
    _log(sdir, "init", source=str(src), **_summary(manifest))
    return f"{site}: initialised from {src}"


def cmd_stage(store: Path, site: str, dst: Path) -> str:
    cur = site_dir(store, site) / "current"
    check_folder(cur)
    if dst.exists():
        shutil.rmtree(dst)
    _copy_files(cur, dst)
    return f"{site}: current copied to {dst}"


def cmd_activate(store: Path, site: str, src: Path, build: str) -> str:
    sdir = site_dir(store, site)
    if not (sdir / "current").exists():
        raise StoreError(f"{site}: no current model (run init first)")
    manifest = check_folder(src)
    kept = _install(sdir, src, _next_slot(sdir / "versions", f"replaced-by-b{build}"))
    _log(sdir, "activate", build=build, kept=kept.name if kept else None, **_summary(manifest))
    return f"{site}: build {build} is now current; previous kept as versions/{kept.name if kept else '-'}"


def cmd_export(store: Path, site: str, dst: Path) -> str:
    cur = site_dir(store, site) / "current"
    manifest = check_folder(cur)
    if dst.exists():
        shutil.rmtree(dst)
    _copy_files(cur, dst)
    check_folder(dst)
    return f"{site}: current exported to {dst} ({json.dumps(_summary(manifest))})"


def cmd_rollback(store: Path, site: str) -> str:
    sdir = site_dir(store, site)
    versions = _versions(sdir)
    if not versions:
        raise StoreError(f"{site}: no earlier version to roll back to")
    newest = versions[-1]
    manifest = check_folder(newest)
    kept = _install(sdir, newest, _next_slot(sdir / "rolled-back", "rolled-back"))
    shutil.rmtree(newest)
    _log(sdir, "rollback", restored=newest.name, kept=kept.name, **_summary(manifest))
    return f"{site}: {newest.name} is current again; the rolled-back model is kept in rolled-back/{kept.name}"


def cmd_status(store: Path, site: str) -> str:
    sdir = site_dir(store, site)
    cur = sdir / "current"
    if not cur.exists():
        return f"{site}: no current model"
    out = {"site": site, "current": _summary(check_folder(cur)),
           "versions": [p.name for p in _versions(sdir)]}
    return json.dumps(out, indent=2)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("command", choices=["init", "stage", "activate", "export", "rollback", "status", "exists"])
    ap.add_argument("--site", required=True)
    ap.add_argument("--store", type=Path, default=Path(os.environ.get("MODEL_STORE", DEFAULT_STORE)))
    ap.add_argument("--from", dest="src", type=Path)
    ap.add_argument("--to", dest="dst", type=Path)
    ap.add_argument("--build", default="manual")
    a = ap.parse_args(argv)
    try:
        if a.command == "exists":
            return 0 if (site_dir(a.store, a.site) / "current").exists() else 1
        if a.command in ("init", "activate") and not a.src:
            raise StoreError(f"{a.command} needs --from")
        if a.command in ("stage", "export") and not a.dst:
            raise StoreError(f"{a.command} needs --to")
        msg = {"init": lambda: cmd_init(a.store, a.site, a.src),
               "stage": lambda: cmd_stage(a.store, a.site, a.dst),
               "activate": lambda: cmd_activate(a.store, a.site, a.src, a.build),
               "export": lambda: cmd_export(a.store, a.site, a.dst),
               "rollback": lambda: cmd_rollback(a.store, a.site),
               "status": lambda: cmd_status(a.store, a.site)}[a.command]()
    except (StoreError, json.JSONDecodeError, OSError) as e:
        print(f"FAIL: {e}")
        return 1
    print(f"OK: {msg}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
