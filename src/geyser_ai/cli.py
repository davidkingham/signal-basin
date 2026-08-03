"""Command-line interface: ingest, backtest, predict."""

from __future__ import annotations

import json as jsonlib

import typer
from rich.console import Console
from rich.table import Table

from .config import DB_PATH, TARGET_GEYSERS

app = typer.Typer(add_completion=False, help="Probabilistic geyser eruption prediction.")
console = Console()


@app.command()
def ingest(
    force: bool = typer.Option(False, "--force", help="Re-download even if cached."),
    version: str = typer.Option(None, "--version", help="Archive date, e.g. 2026-08-03."),
) -> None:
    """Download the GeyserTimes archive and build the DuckDB database."""
    from .ingest import run_ingest

    run_ingest(force_download=force, version=version)


@app.command()
def backtest(
    geyser: list[str] = typer.Option(None, "--geyser", "-g", help="Repeatable; default all."),
    years: int = typer.Option(3, "--years", help="Years of walk-forward evaluation."),
    nowcast: bool = typer.Option(
        True,
        "--nowcast/--no-nowcast",
        help="Also score neighbour-geyser conditioning (slow; Beehive and Grand).",
    ),
) -> None:
    """Run the walk-forward backtest and write reports/calibration_report.md."""
    from .backtest import honest_coverage, run_backtest
    from .report import write_report

    if not DB_PATH.exists():
        raise typer.BadParameter(f"No database at {DB_PATH}. Run `geyser-ai ingest` first.")
    targets = list(geyser) if geyser else list(TARGET_GEYSERS)
    scores, recs = run_backtest(targets, years=years)
    if not scores:
        console.print("[red]No models scored — nothing to report.[/red]")
        raise typer.Exit(1)

    console.print("Scoring honest coverage against all intervals ...")
    honest: dict[str, dict] = {}
    for g in targets:
        try:
            hc = honest_coverage(g, years=years)
        except Exception as exc:
            console.print(f"  {g}: honest coverage failed ({exc})")
            continue
        if hc:
            honest[g] = hc
            console.print(
                f"  {g:<16} n={hc['n']:>5,}  rejected={hc['pct_filtered_out']:4.1f}%  "
                f"50%={hc['cover50']:.1%}  90%={hc['cover90']:.1%}"
            )
    nowcasts: dict[str, dict] = {}
    if nowcast:
        from .nowcast import NEIGHBORS, nowcast_backtest

        for g in [x for x in targets if x in NEIGHBORS]:
            console.print(f"Nowcast (neighbour-conditioned) backtest: {g} ...")
            try:
                res = nowcast_backtest(g, years=2, step_min=30)
            except Exception as exc:
                console.print(f"  {g}: nowcast failed ({exc})")
                continue
            if res:
                nowcasts[g] = res
                o = res["overall"]
                console.print(
                    f"  {g:<10} n={o['n']:,}  CRPS {o['off']['crps']:.1f} -> "
                    f"{o['on']['crps']:.1f} ({o['crps_delta_pct']:+.1f}%)"
                )

    path = write_report(scores, recs, years=years, honest=honest, nowcasts=nowcasts)
    console.print(f"\n[green]Wrote {path}[/green]")


@app.command()
def predict(
    geyser: list[str] = typer.Option(None, "--geyser", "-g", help="Repeatable; default all."),
    model: str = typer.Option(None, "--model", help="Model name; default best_parametric."),
    json: bool = typer.Option(False, "--json", help="Emit JSON only."),
) -> None:
    """Predict the next eruption for each target geyser."""
    from .predict import predict_all

    if not DB_PATH.exists():
        raise typer.BadParameter(f"No database at {DB_PATH}. Run `geyser-ai ingest` first.")
    targets = list(geyser) if geyser else list(TARGET_GEYSERS)
    results = predict_all(targets, model_name=model)
    if not results:
        console.print("[red]No predictions produced.[/red]")
        raise typer.Exit(1)

    if json:
        print(jsonlib.dumps(results, indent=2))
        return

    t = Table(title="Next-eruption predictions", header_style="bold")
    t.add_column("Geyser")
    t.add_column("Last logged (local)")
    t.add_column("Age (h)", justify="right")
    t.add_column("Missed?", justify="right")
    t.add_column("Predicted", justify="left")
    t.add_column("50% window")
    t.add_column("90% window")
    for r in results:
        missed = r["expected_missed_eruptions"]
        missed_cell = f"[yellow]~{missed:.1f}[/yellow]" if r["data_is_stale"] else f"{missed:.1f}"
        t.add_row(
            r["geyser"],
            r["last_eruption_local"],
            f"{r['data_age_hours']:.1f}",
            missed_cell,
            r["predicted_time_local"],
            f"{r['window_50_local'][0][11:]} – {r['window_50_local'][1][11:]}",
            f"{r['window_90_local'][0][11:]} – {r['window_90_local'][1][11:]}",
        )
    console.print(t)
    stale = [r["geyser"] for r in results if r["data_is_stale"]]
    console.print(
        f"[dim]Model: {results[0]['model']} + renewal adjustment. Times America/Denver.\n"
        "'Missed?' is the expected number of eruptions that occurred but were never "
        "logged during the silent window. Where it is above ~0.5 the archive snapshot "
        "is stale rather than the geyser being overdue, and the prediction accounts "
        "for that.[/dim]"
    )
    if stale:
        console.print(
            f"[yellow]Stale data for: {', '.join(stale)} — these are renewal forecasts, "
            "not 'it is due any minute now'.[/yellow]"
        )
    print()
    print(jsonlib.dumps(results, indent=2))


@app.command()
def sync(
    force: bool = typer.Option(False, "--force", help="Ignore the cache TTL."),
    minutes: int = typer.Option(None, "--minutes", help="Explicit lookback window."),
) -> None:
    """Pull recent entries from the GeyserTimes REST API into the database."""
    from .sync import sync_recent

    if not DB_PATH.exists():
        raise typer.BadParameter(f"No database at {DB_PATH}. Run `geyser-ai ingest` first.")
    res = sync_recent(force=force, minutes=minutes)
    if res.get("error"):
        console.print(f"[red]Sync failed:[/red] {res['error']}")
        raise typer.Exit(1)
    if res.get("cached"):
        console.print(
            f"[dim]Cached; next refresh in {res['seconds_until_refresh']}s. "
            f"{res['n_total']} rows held.[/dim]"
        )
        return
    console.print(
        f"[green]Synced[/green] {res['n_last']} entries "
        f"(lookback {res['lookback_min']} min); {res['n_total']} rows held."
    )


@app.command()
def serve(
    host: str = typer.Option("127.0.0.1", "--host"),
    port: int = typer.Option(8000, "--port"),
    reload: bool = typer.Option(False, "--reload", help="Auto-reload for development."),
) -> None:
    """Serve the JSON API and the dashboard."""
    from .api import serve as _serve

    if not DB_PATH.exists():
        raise typer.BadParameter(f"No database at {DB_PATH}. Run `geyser-ai ingest` first.")
    console.print(f"[green]Dashboard:[/green] http://{host}:{port}/")
    console.print(f"[dim]API docs:  http://{host}:{port}/docs[/dim]")
    _serve(host=host, port=port, reload=reload)


@app.command()
def mcp() -> None:
    """Run the MCP server over stdio."""
    from .mcp_server import main as _main

    _main()


if __name__ == "__main__":
    app()
