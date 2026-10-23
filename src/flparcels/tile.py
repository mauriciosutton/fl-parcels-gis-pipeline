"""Step 4: build the PMTiles archive from features.fgb with tippecanoe."""
import logging
import shutil
import subprocess
from pathlib import Path

from .config import MAX_ZOOM, MIN_ZOOM, TILE_LAYER

log = logging.getLogger(__name__)


def tippecanoe_command(
    features: Path, out: Path, tmp_dir: Path, tippecanoe: str = "tippecanoe", extra_args: list[str] = ()
) -> list[str]:
    return [
        tippecanoe,
        f"--output={out}",
        "--force",
        f"--layer={TILE_LAYER}",
        f"--minimum-zoom={MIN_ZOOM}",
        f"--maximum-zoom={MAX_ZOOM}",
        # fid becomes the MVT feature id: clients key feature-state and lookup.parquet on it.
        "--use-attribute-for-id=fid",
        # Dense metros overflow the 500 KB tile limit at the low zooms; thin them there instead of failing.
        "--drop-densest-as-needed",
        "--extend-zooms-if-still-dropping",
        f"--temporary-directory={tmp_dir}",
        "--progress-interval=60",
        "--name=Florida Parcels",
        "--attribution=Florida Department of Revenue",
        "--description=Florida DOR statewide parcel polygons with tax roll attributes",
        *extra_args,
        str(features),
    ]


def tile(
    features: Path, out: Path, tmp_dir: Path, tippecanoe: str = "tippecanoe", extra_args: list[str] = ()
) -> Path:
    exe = shutil.which(tippecanoe)
    if exe is None:
        raise FileNotFoundError(
            f"{tippecanoe!r} not found; run this step in the Docker image or install tippecanoe on a Linux host"
        )
    if not features.exists():
        raise FileNotFoundError(f"{features} is missing; run the transform step first")
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp_dir.mkdir(parents=True, exist_ok=True)
    partial = out.with_name(out.stem + ".partial.pmtiles")  # tippecanoe picks the format from the extension
    cmd = tippecanoe_command(features, partial, tmp_dir, exe, extra_args)
    log.info("running %s", " ".join(cmd))
    subprocess.run(cmd, check=True)
    partial.replace(out)
    log.info("wrote %s (%.2f GB)", out, out.stat().st_size / 1e9)
    return out
