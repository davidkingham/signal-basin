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
) -> None:
    """Run the walk-forward backtest and write reports/calibration_report.md."""
    from .backtest import run_backtest
    from .report import write_report

    if not DB_PATH.exists():
        raise typer.BadParameter(f"No database at {DB_PATH}. Run `geyser-ai ingest` first.")
    targets = list(geyser) if geyser else list(TARGET_GEYSERS)
    scores, recs = run_backtest(targets, years=years)
    if not scores:
        console.print("[red]No models scored — nothing to report.[/red]")
        raise typer.Exit(1)
    path = write_report(scores, recs, years=years)
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
    t.add_column("Last eruption (local)")
    t.add_column("Age (h)", justify="right")
    t.add_column("Predicted", justify="left")
    t.add_column("50% window")
    t.add_column("90% window")
    for r in results:
        t.add_row(
            r["geyser"],
            r["last_eruption_local"],
            f"{r['data_age_hours']:.1f}",
            r["predicted_time_local"],
            f"{r['window_50_local'][0][11:]} – {r['window_50_local'][1][11:]}",
            f"{r['window_90_local'][0][11:]} – {r['window_90_local'][1][11:]}",
        )
    console.print(t)
    console.print(
        f"[dim]Model: {results[0]['model']}. Times America/Denver. Predictions are "
        "anchored to the last eruption present in the local archive snapshot — "
        "check 'Age' before trusting them in the field.[/dim]"
    )
    print()
    print(jsonlib.dumps(results, indent=2))


if __name__ == "__main__":
    app()
