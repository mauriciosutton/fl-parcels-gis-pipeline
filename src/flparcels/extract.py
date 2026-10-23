"""Step 1: unzip the DOR download into data/raw."""
import logging
import shutil
import zipfile
from pathlib import Path

log = logging.getLogger(__name__)


def extract(zip_path: Path, raw_dir: Path, force: bool = False) -> Path:
    """Unzip the single .gdb folder in zip_path into raw_dir; skip if a complete copy is already there."""
    with zipfile.ZipFile(zip_path) as zf:
        members = [m for m in zf.infolist() if not m.is_dir()]
        tops = {Path(m.filename).parts[0] for m in members}
        if len(tops) != 1 or not next(iter(tops)).endswith(".gdb"):
            raise ValueError(f"{zip_path} should hold exactly one .gdb folder, found {sorted(tops)}")
        target = raw_dir / tops.pop()
        expected = sum(m.file_size for m in members)
        if target.exists() and not force and _dir_size(target) == expected:
            log.info("already extracted: %s", target)
            return target

        # Extract beside the target and swap in at the end, so an interrupted run never looks complete.
        # zipfile itself strips absolute paths and '..' components from member names.
        partial = raw_dir / f".{target.name}.partial"
        shutil.rmtree(partial, ignore_errors=True)
        partial.mkdir(parents=True)
        log.info("extracting %s (%.1f GB) -> %s", zip_path, expected / 1e9, target)
        zf.extractall(partial)

    shutil.rmtree(target, ignore_errors=True)
    (partial / target.name).rename(target)
    partial.rmdir()
    return target


def _dir_size(path: Path) -> int:
    return sum(f.stat().st_size for f in path.rglob("*") if f.is_file())
