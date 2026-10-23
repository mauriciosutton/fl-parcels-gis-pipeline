"""Command line entry point: python -m flparcels [--data-dir DIR] <step> [options]."""
import argparse
import json
import logging
from pathlib import Path

from . import extract, publish, stage, tile, transform, validate
from .codes import COUNTIES
from .config import Paths, build_name

log = logging.getLogger("flparcels")


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s", datefmt="%H:%M:%S")
    paths = Paths.resolve(args.data_dir)
    steps = {
        "extract": [_extract],
        "stage": [_stage],
        "transform": [_transform],
        "tile": [_tile],
        "validate": [_validate],
        "publish": [_publish],  # not part of `run`: it sends data off the host
        "run": [_extract, _stage, _transform, _tile, _validate],
    }[args.step]
    try:
        for step in steps:
            step(paths, args)
    except validate.ValidationError as e:
        print(json.dumps(e.report, indent=2))
        log.error("validation failed: %s", e)
        return 1
    except FileNotFoundError as e:  # missing input from an earlier step, or no tippecanoe on this host
        log.error("%s", e)
        return 1
    return 0


def _extract(paths: Paths, args: argparse.Namespace) -> None:
    if args.zip is None:
        if args.step == "extract":
            raise SystemExit("extract needs --zip")
        return  # `run` without --zip reuses whatever is already in raw/
    extract.extract(args.zip, paths.raw, force=args.force)


def _stage(paths: Paths, args: argparse.Namespace) -> None:
    if paths.staged.exists() and not args.force:
        log.info("already staged: %s (use --force to redo)", paths.staged)
        return
    stage.stage(args.gdb or paths.find_gdb(), paths.staged)


def _transform(paths: Paths, args: argparse.Namespace) -> None:
    report = transform.transform(paths.staged, paths.build(build_name(args.county)), args.county, args.memory_limit)
    print(json.dumps(report, indent=2))


def _tile(paths: Paths, args: argparse.Namespace) -> None:
    name = build_name(args.county)
    tile.tile(
        paths.build(name) / transform.FEATURES,
        paths.pmtiles(name),
        paths.tmp / name,
        args.tippecanoe,
        args.tippecanoe_arg,
    )


def _validate(paths: Paths, args: argparse.Namespace) -> None:
    name = build_name(args.county)
    report_path = paths.build(name) / transform.REPORT
    expected = json.loads(report_path.read_text())["features_written"] if report_path.exists() else None
    print(json.dumps(validate.validate(paths.pmtiles(name), expected), indent=2))


def _publish(paths: Paths, args: argparse.Namespace) -> None:
    name = build_name(args.county)
    files = [paths.pmtiles(name), paths.build(name) / transform.LOOKUP]
    keys = publish.publish(name, files, publish.Target.from_env())
    print(json.dumps({"published": keys}, indent=2))


def _county(value: str) -> int:
    county = int(value)
    if county not in COUNTIES:
        raise argparse.ArgumentTypeError(f"{value} is not a DOR county number (11-77)")
    return county


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="flparcels", description="Florida DOR parcels -> vector PMTiles")
    parser.add_argument("--data-dir", help="working directory for all outputs (default: $FLPARCELS_DATA_DIR or ./data)")
    sub = parser.add_subparsers(dest="step", required=True)

    source = argparse.ArgumentParser(add_help=False)
    source.add_argument("--zip", type=Path, help="DOR download, e.g. Cadastral_Statewide.zip")
    source.add_argument("--gdb", type=Path, help="FileGDB to stage (default: the one .gdb in <data-dir>/raw)")
    source.add_argument("--force", action="store_true", help="redo extract/stage even if outputs exist")

    scope = argparse.ArgumentParser(add_help=False)
    scope.add_argument("--county", type=_county, metavar="CO_NO", help="build one county (DOR number 11-77) as a pilot")

    build = argparse.ArgumentParser(add_help=False)
    build.add_argument("--memory-limit", help="DuckDB memory cap for transform, e.g. 12GB (default: 80%% of RAM)")
    build.add_argument("--tippecanoe", default="tippecanoe", help="tippecanoe executable")
    build.add_argument(
        "--tippecanoe-arg", action="append", default=[], metavar="ARG",
        help="extra tippecanoe flag, repeatable, e.g. --tippecanoe-arg=--detect-shared-borders",
    )

    sub.add_parser("extract", parents=[source], help="unzip the download into <data-dir>/raw")
    sub.add_parser("stage", parents=[source], help="convert the FileGDB to GeoParquet")
    sub.add_parser("transform", parents=[scope, build], help="clean, collapse stacks, reproject, write tile inputs")
    sub.add_parser("tile", parents=[scope, build], help="run tippecanoe")
    sub.add_parser("validate", parents=[scope], help="check the PMTiles")
    sub.add_parser("publish", parents=[scope], help="upload the PMTiles and lookup.parquet to an S3-compatible bucket")
    sub.add_parser("run", parents=[source, scope, build], help="all steps in order")
    return parser
