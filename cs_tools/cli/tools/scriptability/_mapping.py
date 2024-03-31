from __future__ import annotations

from typing import TYPE_CHECKING
import csv
import logging
import pathlib

from rich.align import Align
from rich.table import Table
import typer

from cs_tools.cli.dependencies import thoughtspot
from cs_tools.cli.ux import CSToolsApp, rich_console

from .tmlfs import ImportTMLFS

if TYPE_CHECKING:
    from cs_tools.thoughtspot import ThoughtSpot

log = logging.getLogger(__name__)

app = CSToolsApp(
    name="mapping",
    help="Tools for working with TML GUID mappings.",
    invoke_without_command=True,
)


@app.command(dependencies=[thoughtspot], name="details")
def mapping_details(
    ctx: typer.Context,
    path: pathlib.Path = typer.Argument(..., help="Root folder to TML file system", file_okay=False, resolve_path=True),
    source: str = typer.Option(
        ..., help="the source environment the TML came from", rich_help_panel="GUID Mapping Options"
    ),
    dest: str = typer.Option(
        ..., help="the destination environment the TML is importing into", rich_help_panel="GUID Mapping Options"
    ),
    org_override: str = typer.Option(None, "--org", help="the org to use, if any"),
):
    ts = ctx.obj.thoughtspot

    show_mapping_details(ts, path, source, dest, org_override)


def show_mapping_details(
    ts: ThoughtSpot,
    path: pathlib.Path = typer.Argument(..., help="Root folder to TML file system", file_okay=False, resolve_path=True),
    source: str = typer.Argument(
        ..., help="the source environment the TML came from", rich_help_panel="GUID Mapping Options"
    ),
    dest: str = typer.Argument(
        ..., help="the destination environment the TML was importing into", rich_help_panel="GUID Mapping Options"
    ),
    org_override: str = typer.Option(None, "--org", help="the org to use, if any"),
):
    """Function that can be called from other modules to show the mapping details."""

    if org_override is not None:
        ts.org.switch(org_override)

    # get the tmlfs object from the path
    tmlfs = ImportTMLFS(path, log)
    mappings = tmlfs.read_mapping_file(source, dest).generate_mapping(source, dest)

    mapping_details = {}  # "sourceGUID": { "name": "name", "type: "type", "sourceGUID": "guid", "destGUID": "guid" }
    # for each of the mappings in the tmlfs file, get the type and name from the org specified and put it all into a
    # dictionary
    for sourceGUID, destGUID in mappings.items():
        # print(f"{destGUID} => {sourceGUID}")
        mapping_details[destGUID] = {"sourceGUID": sourceGUID, "destGUID": destGUID}

    # get the type and name for each metadata object.
    for guid in mappings.values():
        try:
            obj = ts.metadata.get([guid])[0]  # Get one at a time in case some no longer exist.
            mapping_details[obj["id"]]["name"] = obj["name"]
            mapping_details[obj["id"]]["type"] = obj["metadata_type"]
        except Exception as e:
            log.error(f"Error getting {guid} from TS: {e}")
            mapping_details[guid]["name"] = "NOT FOUND"
            mapping_details[guid]["type"] = "UNKNOWN"

    _show_results_as_table(mapping_details, source, dest)

    try:
        write_to_file(tmlfs, mapping_details, source, dest)
    except OSError as ioe:
        log.error(f"Error writing to details file: {ioe}")


def _show_results_as_table(mapping_details: dict[str, dict], source: str, dest: str) -> None:
    NAME_COL_WIDTH = 20
    GUID_COL_WIDTH = 36
    TYPE_COL_WIDTH = 15
    TABLE_COL_WIDTH = NAME_COL_WIDTH + (GUID_COL_WIDTH * 2) + TYPE_COL_WIDTH

    table = Table(title=f"Mappings from {source} to {dest}", width=TABLE_COL_WIDTH)

    table.add_column("Name", justify="left", width=NAME_COL_WIDTH)
    table.add_column(f"{source} GUID", justify="left", width=GUID_COL_WIDTH)
    table.add_column(f"{dest} GUID", justify="left", width=GUID_COL_WIDTH)
    table.add_column("Type", justify="left", width=TYPE_COL_WIDTH)

    for _ in mapping_details.values():
        table.add_row(
            _.get("name", ""),
            _.get("sourceGUID", ""),
            _.get("destGUID", ""),
            _.get("type", ""),
        )

    rich_console.print(Align.left(table))


def write_to_file(tmlfs: ImportTMLFS, mapping_details: dict[str, dict], source: str, dest: str) -> None:
    """Write the mapping details to a file in the guid-mappings folder."""

    csvfile = tmlfs.path / "guid-mappings" / f"{source}_{dest}_mapping_details.csv"

    log.info(f"Writing mapping details to {csvfile}")

    with open(csvfile, "w", newline="") as out:
        writer = csv.writer(out, delimiter=",", quotechar='"', quoting=csv.QUOTE_MINIMAL)

        writer.writerow(["Name", "Source GUID", "Destination GUID", "Type"])

        for _ in mapping_details.values():
            writer.writerow([_.get("name", ""), _.get("sourceGUID", ""), _.get("destGUID", ""), _.get("type", "")])
