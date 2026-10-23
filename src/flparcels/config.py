"""Paths and build settings shared by the pipeline steps."""
import os
from dataclasses import dataclass
from pathlib import Path

SOURCE_LAYER = "CADASTRAL_DOR"
TILE_LAYER = "parcels"
MIN_ZOOM = 12
MAX_ZOOM = 16
MAX_TILE_BYTES = 500_000  # tippecanoe's default limit on a compressed tile

# Loose lon/lat envelope around Florida, used to sanity-check outputs.
FLORIDA_BOUNDS = (-87.7, 24.3, -79.8, 31.1)


def build_name(county: int | None) -> str:
    return "fl_parcels" if county is None else f"fl_parcels_co{county}"


@dataclass(frozen=True)
class Paths:
    data_dir: Path

    @classmethod
    def resolve(cls, data_dir: str | Path | None = None) -> "Paths":
        return cls(Path(data_dir or os.environ.get("FLPARCELS_DATA_DIR", "data")).resolve())

    @property
    def raw(self) -> Path:
        return self.data_dir / "raw"

    @property
    def staged(self) -> Path:
        return self.data_dir / "staging" / "parcels.parquet"

    @property
    def tmp(self) -> Path:
        return self.data_dir / "tmp"

    def build(self, name: str) -> Path:
        return self.data_dir / "build" / name

    def pmtiles(self, name: str) -> Path:
        return self.data_dir / "out" / f"{name}.pmtiles"

    def find_gdb(self) -> Path:
        gdbs = sorted(self.raw.glob("*.gdb"))
        if len(gdbs) != 1:
            raise FileNotFoundError(f"expected exactly one .gdb in {self.raw}, found {len(gdbs)}")
        return gdbs[0]
