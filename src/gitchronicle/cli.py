"""gitchronicle command-line interface (domain-centric).

Pipeline: extract -> signals -> untangle -> catalog -> attribute -> lifecycle ->
index -> graph. Every stage is resumable. Query the result with `domains` / `show`.
Config from config.toml; --repo/--rev/--db override it.
"""

from __future__ import annotations

import os
import sys
from importlib.metadata import PackageNotFoundError, version as _pkg_version
from pathlib import Path
from typing import Optional

import typer
from rich.console import Console
from rich.table import Table

from .attribute import attribute
from .catalog import catalog
from .chronicle import chronicle
from .untangle import untangle
from .config import load_config
from .discover import discover
from .enrich import enrich
from .extract import ingest
from .extract.git_ingest import run_git
from .index import build_index
from .lifecycle import lifecycle
from .llm import build_provider
from .query import keyword_commits, keyword_domains, resolve_domain, semantic
from .serve import export_graph
from .storage import connect, init_db

console = Console()
app = typer.Typer(add_completion=False, no_args_is_help=True,
                  help="Reconstruct a codebase's domains and their history from git.")


def _version() -> str:
    try:
        return _pkg_version("gitchronicle")
    except PackageNotFoundError:
        return "0.1.0"


def _log(msg: str = "") -> None:
    console.print(msg, markup=False, highlight=False)


def _head(title: str) -> None:
    console.print(f"[bold cyan]▸ {title}[/]")


def _untangle_kwargs(cfg) -> dict:
    """Thread the [untangle] config through to untangle() (known params only)."""
    keys = ("max_diff_lines", "max_files", "workers", "max_line_chars", "max_diff_chars",
            "max_msg_files", "min_subject_len")
    u = cfg.get("untangle", {})
    return {k: u[k] for k in keys if k in u}


def _setup(config, repo, rev, db):
    cfg = load_config(config)
    if repo:
        cfg["repo"]["path"] = repo
    if rev:
        cfg["repo"]["rev_range"] = rev
    if db:
        cfg["db"]["path"] = db
    conn = connect(cfg["db"]["path"])
    init_db(conn)
    return cfg, conn


_Config = typer.Option("config.toml", "--config", "-c", help="Path to config.toml")
_Repo = typer.Option(None, "--repo", help="Target git repository")
_Rev = typer.Option(None, "--rev", help="Git rev-range to analyse (one branch overlay)")
_Db = typer.Option(None, "--db", help="SQLite DB path")


def _version_cb(value: bool):
    if value:
        console.print(_version())
        raise typer.Exit()


@app.callback()
def _main(version: bool = typer.Option(False, "--version", callback=_version_cb,
                                       is_eager=True, help="Show version and exit")):
    """gitchronicle: git history -> domain knowledge base."""


# ---- pipeline stages --------------------------------------------------------
@app.command()
def extract(config: str = _Config, repo: str = _Repo, rev: str = _Rev, db: str = _Db):
    """Ingest commits, file churn, branches and tags for the rev-range."""
    cfg, conn = _setup(config, repo, rev, db)
    _head("Extract")
    ingest(conn, cfg["repo"]["path"], cfg["repo"]["rev_range"], log=_log)


@app.command()
def signals(config: str = _Config, repo: str = _Repo, rev: str = _Rev, db: str = _Db):
    """Enrich commits: language, conventional-commit, work-kind, components."""
    cfg, conn = _setup(config, repo, rev, db)
    _head("Signals")
    enrich(conn, log=_log)


@app.command(name="untangle")
def untangle_cmd(config: str = _Config, repo: str = _Repo, rev: str = _Rev, db: str = _Db,
                 force: bool = typer.Option(False, "--force", help="Re-untangle all commits")):
    """Untangle each commit's diff into semantic concerns (LLM; message untrusted)."""
    cfg, conn = _setup(config, repo, rev, db)
    _head("Untangle")
    provider = build_provider(cfg, conn)
    untangle(conn, provider, cfg["repo"]["path"], log=_log, force=force,
             **_untangle_kwargs(cfg))


@app.command()
def catalog_cmd(config: str = _Config, repo: str = _Repo, rev: str = _Rev, db: str = _Db,
                frozen: bool = typer.Option(False, "--frozen",
                    help="Classify strictly against the existing taxonomy; never mint new features"),
                reinduce: bool = typer.Option(False, "--reinduce",
                    help="Rebuild the taxonomy from scratch (drops unlocked features)"),
                reclassify: bool = typer.Option(False, "--reclassify",
                    help="Re-run classification for ALL concerns (human assignments kept)")):
    """Build/apply the feature taxonomy: induce once, then classify concerns by ID."""
    cfg, conn = _setup(config, repo, rev, db)
    _head("Catalog")
    cfg.setdefault("catalog", {})["frozen"] = frozen
    cfg["catalog"]["reinduce"] = reinduce
    cfg["catalog"]["reclassify"] = reclassify
    provider = build_provider(cfg, conn)
    head = run_git(cfg["repo"]["path"], ["rev-parse", "HEAD"]).strip()
    catalog(conn, provider, cfg, cfg["repo"]["rev_range"], head, log=_log)


app.command(name="catalog")(catalog_cmd)


@app.command()
def attribute_cmd(config: str = _Config, repo: str = _Repo, rev: str = _Rev, db: str = _Db):
    """Attach commits to domains (many-to-many) by the files they touch."""
    _, conn = _setup(config, repo, rev, db)
    _head("Attribute")
    attribute(conn, log=_log)


app.command(name="attribute")(attribute_cmd)


@app.command()
def lifecycle_cmd(config: str = _Config, repo: str = _Repo, rev: str = _Rev, db: str = _Db):
    """Detect domain lifecycle (active/dormant/merged/removed) vs the ref tip."""
    cfg, conn = _setup(config, repo, rev, db)
    _head("Lifecycle")
    head = run_git(cfg["repo"]["path"], ["rev-parse", "HEAD"]).strip()
    lifecycle(conn, cfg["repo"]["path"], head, cfg, log=_log)


app.command(name="lifecycle")(lifecycle_cmd)


@app.command()
def discover_cmd(config: str = _Config, repo: str = _Repo, rev: str = _Rev, db: str = _Db,
                 force: bool = typer.Option(False, "--force", help="Re-name already-named domains"),
                 limit: Optional[int] = typer.Option(None, "--limit", help="Only the N largest domains")):
    """Name domains and infer what/why/evolution with the LLM (from diffs)."""
    cfg, conn = _setup(config, repo, rev, db)
    _head("Discover")
    provider = build_provider(cfg, conn)
    discover(conn, provider, cfg, cfg["repo"]["path"], log=_log, force=force, limit=limit)


app.command(name="discover")(discover_cmd)


@app.command()
def graph(config: str = _Config, repo: str = _Repo, rev: str = _Rev, db: str = _Db,
          out: Optional[str] = typer.Option(None, "--out", help="HTML output path"),
          json_out: Optional[str] = typer.Option(None, "--json", help="features.json output path")):
    """Export the self-contained HTML domain graph and JSON."""
    cfg, conn = _setup(config, repo, rev, db)
    _head("Graph")
    export_graph(conn, out or cfg["output"]["html"], json_out or cfg["output"]["json"], log=_log)


@app.command(name="chronicle")
def chronicle_cmd(config: str = _Config, repo: str = _Repo, rev: str = _Rev, db: str = _Db,
                  force: bool = typer.Option(False, "--force", help="Rebuild all chapters")):
    """Build the narrative evolution chronicle (per domain + repo). LLM; opt-in."""
    cfg, conn = _setup(config, repo, rev, db)
    _head("Chronicle")
    provider = build_provider(cfg, conn)
    chronicle(conn, provider, cfg["repo"]["path"], log=_log, force=force)


@app.command()
def run(config: str = _Config, repo: str = _Repo, rev: str = _Rev, db: str = _Db,
        chronicle_flag: bool = typer.Option(False, "--chronicle",
            help="Also build the narrative evolution chronicle (LLM; opt-in)"),
        frozen: bool = typer.Option(False, "--frozen",
            help="Gated mode: classify strictly against the existing taxonomy, never mint "
                 "features; exit 2 when changes await review (CI convention)"),
        out: Optional[str] = typer.Option(None, "--out", help="HTML output path"),
        json_out: Optional[str] = typer.Option(None, "--json", help="JSON output path")):
    """Run the pipeline. Default = structural map; add --chronicle for the narrative."""
    cfg, conn = _setup(config, repo, rev, db)
    repo_path, rev_range = cfg["repo"]["path"], cfg["repo"]["rev_range"]
    console.print(f"[bold]gitchronicle[/] · repo={repo_path} · rev={rev_range}")
    head = run_git(repo_path, ["rev-parse", "HEAD"]).strip()
    cfg.setdefault("catalog", {})["frozen"] = frozen
    provider = build_provider(cfg, conn)

    _head("Extract"); ingest(conn, repo_path, rev_range, log=_log)
    _head("Signals"); enrich(conn, log=_log)
    _head("Untangle"); untangle(conn, provider, repo_path, log=_log, **_untangle_kwargs(cfg))
    _head("Catalog"); catalog(conn, provider, cfg, rev_range, head, log=_log)
    _head("Attribute"); attribute(conn, log=_log)
    if cfg.get("catalog", {}).get("method", "taxonomy") == "taxonomy":
        # domains + areas come out of the taxonomy already named; just roll up commit stats.
        from .taxonomy import rollups
        rollups(conn)
    else:
        _head("Discover"); discover(conn, provider, cfg, repo_path, log=_log)  # names + merges
    if chronicle_flag:
        _head("Chronicle"); chronicle(conn, provider, repo_path, log=_log)
    _head("Lifecycle"); lifecycle(conn, repo_path, head, cfg, log=_log)
    _head("Index"); build_index(conn, provider, log=_log)
    _head("Graph")
    res = export_graph(conn, out or cfg["output"]["html"], json_out or cfg["output"]["json"], log=_log)
    console.print(f"\n[bold green]Done.[/] {res.get('domains')} domains. "
                  f"Query with [bold]gitchronicle ask \"...\"[/] or open [bold]{res.get('html')}[/].")

    # Never block on a human: report drift, exit 2 only in gated (--frozen) mode.
    from .taxonomy import pending_changeset, render_changeset
    changes = pending_changeset(conn)
    unassigned = conn.execute(
        "SELECT COUNT(*) FROM concerns WHERE domain_id IS NULL AND label IS NOT NULL").fetchone()[0]
    if changes:
        console.print("\n" + render_changeset(changes), markup=False, highlight=False)
    if unassigned:
        console.print(f"{unassigned} concerns fit no existing feature"
                      + (" (frozen: left unassigned)" if frozen else ""))
    if frozen and (changes or unassigned):
        raise typer.Exit(2)   # terraform-style: 0 = clean, 2 = changes awaiting review


@app.command()
def ground(config: str = _Config, repo: str = _Repo, rev: str = _Rev, db: str = _Db,
           force: bool = typer.Option(False, "--force", help="Rebuild census + glossary")):
    """Build the repo-level evidence layer: stem census + doc harvest + feature glossary."""
    from .taxonomy.ground import ground as ground_stage
    cfg, conn = _setup(config, repo, rev, db)
    _head("Ground")
    provider = build_provider(cfg, conn)
    ground_stage(conn, provider, cfg["repo"]["path"], cfg, log=_log, force=force)


@app.command()
def check(config: str = _Config, repo: str = _Repo, rev: str = _Rev, db: str = _Db):
    """Taxonomy health metrics: stem coherence, near-dups, confidence, coverage."""
    from .taxonomy.check import print_health
    _, conn = _setup(config, repo, rev, db)
    _head("Check")
    print_health(conn, log=_log)


@app.command()
def inspect(config: str = _Config, repo: str = _Repo, rev: str = _Rev, db: str = _Db,
            out: str = typer.Option("build/inspect.html", "--out", help="Output HTML path")):
    """Export the per-stage validation GUI (self-contained inspect.html, read-only)."""
    from .serve.inspect_export import export_inspect
    _, conn = _setup(config, repo, rev, db)
    _head("Inspect")
    Path(out).parent.mkdir(parents=True, exist_ok=True)
    export_inspect(conn, out, log=_log)


# ---- taxonomy: the optional human review seam --------------------------------
tax_app = typer.Typer(add_completion=False, no_args_is_help=True,
                      help="Inspect and curate the feature taxonomy (optional review seam; "
                           "the pipeline never blocks on it).")
app.add_typer(tax_app, name="taxonomy")


@tax_app.command("list")
def tax_list(config: str = _Config, repo: str = _Repo, rev: str = _Rev, db: str = _Db,
             status: Optional[str] = typer.Option(None, "--status", help="Filter by status")):
    """List the taxonomy: features, status, size."""
    _, conn = _setup(config, repo, rev, db)
    q = ("SELECT d.id, d.name, d.status, d.locked, d.n_commits, a.name AS area, "
         "(SELECT COUNT(*) FROM concerns c WHERE c.domain_id=d.id) AS n_concerns "
         "FROM domains d LEFT JOIN areas a ON a.id=d.area_id "
         "WHERE d.status IN ('named','provisional','confirmed')")
    params = []
    if status:
        q += " AND d.status=?"; params.append(status)
    rows = conn.execute(q + " ORDER BY a.name, n_concerns DESC", params).fetchall()
    t = Table(title=f"taxonomy ({len(rows)} features)")
    for col in ("id", "feature", "status", "area", "concerns", "commits"):
        t.add_column(col)
    for r in rows:
        st = r["status"] + (" 🔒" if r["locked"] else "")
        t.add_row(str(r["id"]), r["name"], st, r["area"] or "", str(r["n_concerns"]),
                  str(r["n_commits"] or 0))
    console.print(t)


@tax_app.command("show")
def tax_show(name: str, config: str = _Config, repo: str = _Repo, rev: str = _Rev, db: str = _Db):
    """One feature: definition, sample concerns, files."""
    from .taxonomy import resolve_feature
    _, conn = _setup(config, repo, rev, db)
    d = resolve_feature(conn, name)
    if not d:
        console.print(f"[red]no feature matching[/] {name!r}")
        raise typer.Exit(1)
    console.print(f"[bold]{d['name']}[/]  [{d['status']}{' · locked' if d['locked'] else ''}]  id={d['id']}")
    console.print(f"\n[bold]Definition[/]  {d['definition'] or '(none)'}")
    console.print("\n[bold]Sample concerns[/]")
    for r in conn.execute("SELECT label, assign_source, assign_conf FROM concerns "
                          "WHERE domain_id=? ORDER BY id DESC LIMIT 12", (d["id"],)):
        conf = f" ({r['assign_conf']:.2f})" if r["assign_conf"] else ""
        console.print(f"  · {r['label']}  [dim][{r['assign_source'] or '?'}{conf}][/]")
    console.print("\n[bold]Top files[/]")
    for r in conn.execute("SELECT path FROM domain_files WHERE domain_id=? "
                          "ORDER BY weight DESC LIMIT 10", (d["id"],)):
        console.print(f"  · {r['path']}")


@tax_app.command("review")
def tax_review(config: str = _Config, repo: str = _Repo, rev: str = _Rev, db: str = _Db,
               edit: bool = typer.Option(False, "--edit",
                   help="Open the pending changeset as an editable plan in $EDITOR"),
               apply_file: Optional[str] = typer.Option(None, "--apply",
                   help="Apply an edited plan file (non-interactive / CI flow)"),
               as_json: bool = typer.Option(False, "--json",
                   help="Print the pending changeset as JSON (for automation wrappers)")):
    """Review pending taxonomy proposals. Plain call prints the changeset; --edit opens a
    rebase-i-style plan; --apply applies a previously edited plan file; --json emits the
    changeset machine-readably (always exit 0 — the count is in the payload)."""
    import json as _json
    import subprocess as sp
    from .taxonomy import (apply_actions, parse_plan, pending_changeset, render_changeset,
                           review_plan)
    cfg, conn = _setup(config, repo, rev, db)

    if as_json:
        changes = pending_changeset(conn)
        print(_json.dumps({"pending": len(changes), "proposals": changes},
                          indent=2, ensure_ascii=False))
        return

    if apply_file:
        actions = parse_plan(Path(apply_file).read_text(encoding="utf-8"))
        if not actions:
            console.print("no actions in plan file")
            raise typer.Exit(0)
        provider = build_provider(cfg, conn)
        counts = apply_actions(conn, provider, cfg, actions, log=_log)
        console.print(f"[green]applied:[/] " + ", ".join(f"{k} {v}" for k, v in counts.items() if v))
        return

    changes = pending_changeset(conn)
    if not changes:
        console.print("taxonomy: no pending proposals")
        raise typer.Exit(0)
    if not edit:
        console.print(render_changeset(changes), markup=False, highlight=False)
        return

    plan_path = Path(cfg["db"]["path"]).with_name("taxonomy-review.txt")
    plan_path.write_text(review_plan(changes), encoding="utf-8")
    editor = os.environ.get("EDITOR") or os.environ.get("VISUAL")
    if not editor or not sys.stdin.isatty():
        console.print(f"plan written to [bold]{plan_path}[/] — edit it, then run\n"
                      f"  gitchronicle taxonomy review --apply {plan_path}")
        return
    sp.call([editor, str(plan_path)])
    actions = parse_plan(plan_path.read_text(encoding="utf-8"))
    provider = build_provider(cfg, conn)
    counts = apply_actions(conn, provider, cfg, actions, log=_log)
    console.print("[green]applied:[/] " + ", ".join(f"{k} {v}" for k, v in counts.items() if v))


@tax_app.command("merge")
def tax_merge(src: str, dst: str, config: str = _Config, repo: str = _Repo, rev: str = _Rev,
              db: str = _Db):
    """Fold feature SRC into DST (concerns repoint; SRC name becomes an alias)."""
    from .taxonomy import apply_actions, resolve_feature
    cfg, conn = _setup(config, repo, rev, db)
    s = resolve_feature(conn, src)
    if not s:
        console.print(f"[red]no feature matching[/] {src!r}"); raise typer.Exit(1)
    provider = build_provider(cfg, conn)
    apply_actions(conn, provider, cfg, [{"verb": "merge", "id": s["id"], "target": dst}], log=_log)


@tax_app.command("rename")
def tax_rename(name: str, new_name: str, config: str = _Config, repo: str = _Repo,
               rev: str = _Rev, db: str = _Db):
    """Rename a feature (old name kept as alias)."""
    from .taxonomy import apply_actions, resolve_feature
    cfg, conn = _setup(config, repo, rev, db)
    d = resolve_feature(conn, name)
    if not d:
        console.print(f"[red]no feature matching[/] {name!r}"); raise typer.Exit(1)
    apply_actions(conn, None, cfg, [{"verb": "rename", "id": d["id"], "target": new_name}], log=_log)


@tax_app.command("reject")
def tax_reject(name: str, config: str = _Config, repo: str = _Repo, rev: str = _Rev, db: str = _Db):
    """Reject a feature: tombstone the name (never re-proposed), re-home its concerns."""
    from .taxonomy import apply_actions, resolve_feature
    cfg, conn = _setup(config, repo, rev, db)
    d = resolve_feature(conn, name)
    if not d:
        console.print(f"[red]no feature matching[/] {name!r}"); raise typer.Exit(1)
    provider = build_provider(cfg, conn)
    apply_actions(conn, provider, cfg, [{"verb": "reject", "id": d["id"]}], log=_log)


@tax_app.command("confirm")
def tax_confirm(name: str, config: str = _Config, repo: str = _Repo, rev: str = _Rev, db: str = _Db,
                lock: bool = typer.Option(False, "--lock",
                    help="Also lock: auto-runs may never rename/merge/drop it")):
    """Confirm a (provisional) feature as reviewed-correct."""
    from .taxonomy import apply_actions, resolve_feature
    cfg, conn = _setup(config, repo, rev, db)
    d = resolve_feature(conn, name)
    if not d:
        console.print(f"[red]no feature matching[/] {name!r}"); raise typer.Exit(1)
    apply_actions(conn, None, cfg, [{"verb": "lock" if lock else "accept", "id": d["id"]}], log=_log)


@tax_app.command("export")
def tax_export(path: str = typer.Argument("taxonomy.toml"), config: str = _Config,
               repo: str = _Repo, rev: str = _Rev, db: str = _Db):
    """Export the taxonomy to a reviewable TOML file (PR-based review flow)."""
    from .taxonomy import export_taxonomy
    _, conn = _setup(config, repo, rev, db)
    export_taxonomy(conn, path, log=_log)


@tax_app.command("import")
def tax_import(path: str, config: str = _Config, repo: str = _Repo, rev: str = _Rev, db: str = _Db):
    """Import an edited taxonomy TOML: updates matched features, creates new ones; never deletes."""
    from .taxonomy import import_taxonomy
    _, conn = _setup(config, repo, rev, db)
    import_taxonomy(conn, path, log=_log)


# ---- queries ----------------------------------------------------------------
def _resolve_asof(conn, asof: Optional[str]) -> Optional[str]:
    if not asof:
        return None
    row = conn.execute("SELECT time_start FROM eras WHERE name=?", (asof,)).fetchone()
    return (row["time_start"][:10] if row else asof)


@app.command()
def domains(config: str = _Config, repo: str = _Repo, rev: str = _Rev, db: str = _Db,
            as_of: Optional[str] = typer.Option(None, "--as-of", help="Only domains alive at a tag or YYYY-MM-DD"),
            state: Optional[str] = typer.Option(None, "--lifecycle", help="Filter by lifecycle state")):
    """List domains (optionally as of a tag/date, or by lifecycle)."""
    _, conn = _setup(config, repo, rev, db)
    q = ("SELECT name, classification, lifecycle, n_commits, n_files, first_seen, last_seen, "
         "removed_at, confidence FROM domains WHERE status != 'rejected'")
    params: list = []
    asof = _resolve_asof(conn, as_of)
    if asof:
        q += " AND date(first_seen) <= date(?) AND (removed_at IS NULL OR date(removed_at) > date(?))"
        params += [asof, asof]
    if state:
        q += " AND lifecycle = ?"; params.append(state)
    q += " ORDER BY n_commits DESC"
    rows = conn.execute(q, params).fetchall()

    t = Table(title=f"domains{f' as of {asof}' if asof else ''} ({len(rows)})")
    for col in ("domain", "class", "lifecycle", "commits", "files", "span", "conf"):
        t.add_column(col)
    for r in rows:
        span = f"{(r['first_seen'] or '')[:10]}→{(r['removed_at'] or r['last_seen'] or '')[:10]}"
        t.add_row(r["name"] or "?", r["classification"] or "", r["lifecycle"] or "",
                  str(r["n_commits"] or 0), str(r["n_files"] or 0), span, f"{r['confidence'] or 0:.2f}")
    console.print(t)


@app.command()
def show(name: str, config: str = _Config, repo: str = _Repo, rev: str = _Rev, db: str = _Db):
    """Show one domain: what/why/evolution, who, when, files, commits, relations."""
    _, conn = _setup(config, repo, rev, db)
    d = conn.execute(
        "SELECT * FROM domains WHERE status!='rejected' AND (slug=? OR name LIKE ?) "
        "ORDER BY n_commits DESC LIMIT 1", (name, f"%{name}%")).fetchone()
    if not d:
        console.print(f"[red]no domain matching[/] {name!r}")
        raise typer.Exit(1)
    console.print(f"[bold]{d['name']}[/]  [{d['classification'] or '?'} · {d['lifecycle']}]  "
                  f"{(d['first_seen'] or '')[:10]} → {(d['removed_at'] or d['last_seen'] or '')[:10]}  "
                  f"· {d['n_commits']} commits, {d['n_files']} files · depended-on-by {d['fan_in'] or 0}  "
                  f"(conf {d['confidence'] or 0:.2f})")
    console.print(f"\n[bold]What[/]  {d['summary'] or '— (run with --chronicle to generate)'}")
    chapters = conn.execute(
        "SELECT period_start, period_end, title, narrative FROM evolution_chapters "
        "WHERE target_type='domain' AND target_id=? ORDER BY seq", (str(d["id"]),)).fetchall()
    if chapters:
        console.print("\n[bold]Chronicle — how it evolved[/]")
        for ch in chapters:
            console.print(f"  [dim]{(ch['period_start'] or '')[:10]} → {(ch['period_end'] or '')[:10]}[/]"
                          f"  [bold]{ch['title'] or ''}[/]")
            console.print(f"    {ch['narrative'] or ''}")

    who = conn.execute("SELECT c.author_name, COUNT(*) n FROM commit_domains cd "
                       "JOIN commits c ON c.hash=cd.commit_hash WHERE cd.domain_id=? "
                       "GROUP BY c.author_name ORDER BY n DESC LIMIT 6", (d["id"],)).fetchall()
    console.print("\n[bold]Who[/]   " + ", ".join(f"{r['author_name']} ({r['n']})" for r in who))
    kinds = conn.execute("SELECT kind, COUNT(*) n FROM commit_domains WHERE domain_id=? AND kind IS NOT NULL "
                         "GROUP BY kind ORDER BY n DESC", (d["id"],)).fetchall()
    console.print("[bold]Kinds[/] " + ", ".join(f"{r['kind']} ({r['n']})" for r in kinds))

    edges = conn.execute(
        "SELECT de.type, de.why, d2.name FROM domain_edges de JOIN domains d2 "
        "ON d2.id = CASE WHEN de.src_domain=? THEN de.dst_domain ELSE de.src_domain END "
        "WHERE de.src_domain=? OR de.dst_domain=?", (d["id"], d["id"], d["id"])).fetchall()
    if edges:
        console.print("\n[bold]Relations[/]")
        for e in edges:
            console.print(f"  · {e['type']} → {e['name']}" + (f"  ({e['why']})" if e["why"] else ""))

    console.print("\n[bold]Recent commits[/]")
    for r in conn.execute(
        "SELECT c.hash, c.authored_at, cd.kind, c.subject FROM commit_domains cd "
        "JOIN commits c ON c.hash=cd.commit_hash WHERE cd.domain_id=? "
        "ORDER BY c.authored_at DESC LIMIT 12", (d["id"],)):
        console.print(f"  {r['hash'][:10]} {(r['authored_at'] or '')[:10]} "
                      f"[{r['kind'] or '?'}] {r['subject'][:70]}")

    anns = conn.execute(
        "SELECT field, new_value, note, kind FROM annotations "
        "WHERE target_type='domain' AND target_id=? ORDER BY created_at", (str(d["id"]),)).fetchall()
    if anns:
        console.print("\n[bold]Annotations[/]")
        for a in anns:
            head = f"{a['field']}={a['new_value']} " if a["field"] else ""
            console.print(f"  · [{a['kind']}] {head}{a['note'] or ''}")


@app.command()
def annotate(name: str, config: str = _Config, repo: str = _Repo, rev: str = _Rev, db: str = _Db,
             set_name: Optional[str] = typer.Option(None, "--name", help="Correct the domain name"),
             classification: Optional[str] = typer.Option(None, "--classification", help="Set classification"),
             note: Optional[str] = typer.Option(None, "--note", help="Free-text annotation"),
             confirm: bool = typer.Option(False, "--confirm", help="Mark reviewed and lock from auto re-runs")):
    """Annotate/correct a domain (human review). --confirm locks it so auto-runs never overwrite it."""
    from .storage import now_iso
    _, conn = _setup(config, repo, rev, db)
    d = conn.execute("SELECT * FROM domains WHERE status!='rejected' AND (slug=? OR name LIKE ?) "
                     "ORDER BY n_commits DESC LIMIT 1", (name, f"%{name}%")).fetchone()
    if not d:
        console.print(f"[red]no domain matching[/] {name!r}")
        raise typer.Exit(1)

    def _ann(field, old, new, kind):
        conn.execute(
            "INSERT INTO annotations (target_type,target_id,field,old_value,new_value,note,author,kind,created_at) "
            "VALUES ('domain',?,?,?,?,?,?,?,?)",
            (str(d["id"]), field, None if old is None else str(old),
             None if new is None else str(new), note, "user", kind, now_iso()))

    if set_name:
        _ann("name", d["name"], set_name, "correction")
        conn.execute("UPDATE domains SET name=? WHERE id=?", (set_name, d["id"]))
    if classification:
        _ann("classification", d["classification"], classification, "correction")
        conn.execute("UPDATE domains SET classification=? WHERE id=?", (classification, d["id"]))
    if note and not (set_name or classification):
        _ann(None, None, None, "note")
    if confirm:
        _ann("status", d["status"], "confirmed", "confirmation")
        conn.execute("UPDATE domains SET status='confirmed', locked=1 WHERE id=?", (d["id"],))
    conn.commit()
    console.print(f"[green]annotated[/] {set_name or d['name']}"
                  + (" (confirmed & locked)" if confirm else ""))


@app.command(name="index")
def index_cmd(config: str = _Config, repo: str = _Repo, rev: str = _Rev, db: str = _Db):
    """Build the semantic + full-text search indexes over the knowledge base."""
    cfg, conn = _setup(config, repo, rev, db)
    _head("Index")
    provider = build_provider(cfg, conn)
    build_index(conn, provider, log=_log)


@app.command()
def features(config: str = _Config, repo: str = _Repo, rev: str = _Rev, db: str = _Db):
    """List the project's features/domains — what was built."""
    _, conn = _setup(config, repo, rev, db)
    rows = conn.execute(
        "SELECT name, classification, summary, n_commits, first_seen, last_seen "
        "FROM domains WHERE status != 'rejected' ORDER BY n_commits DESC").fetchall()
    console.print(f"[bold]{len(rows)} domains[/]\n")
    for r in rows:
        console.print(f"[bold]{r['name']}[/]  [dim][{r['classification'] or '?'}] · "
                      f"{r['n_commits']}c · {(r['first_seen'] or '')[:10]}→{(r['last_seen'] or '')[:10]}[/]")
        if r["summary"]:
            console.print(f"  {r['summary']}")


@app.command()
def who(name: str, config: str = _Config, repo: str = _Repo, rev: str = _Rev, db: str = _Db):
    """Who worked on a feature/domain — authors, when, and why it exists."""
    cfg, conn = _setup(config, repo, rev, db)
    provider = build_provider(cfg, conn)
    did = resolve_domain(conn, provider, name)
    if did is None:
        console.print(f"[red]no domain matching[/] {name!r}")
        raise typer.Exit(1)
    d = conn.execute("SELECT * FROM domains WHERE id=?", (did,)).fetchone()
    console.print(f"[bold]{d['name']}[/] — {d['summary'] or '(no chronicle summary)'}")
    console.print("\n[bold]Who worked on it[/]")
    for a in conn.execute(
        "SELECT c.author_name, COUNT(*) n, MIN(c.authored_at) f, MAX(c.authored_at) l "
        "FROM commit_domains cd JOIN commits c ON c.hash=cd.commit_hash "
        "WHERE cd.domain_id=? GROUP BY c.author_name ORDER BY n DESC", (did,)):
        console.print(f"  {(a['author_name'] or '?'):24} {a['n']:4} commits   "
                      f"{(a['f'] or '')[:10]} → {(a['l'] or '')[:10]}")


@app.command()
def ask(question: str, config: str = _Config, repo: str = _Repo, rev: str = _Rev, db: str = _Db):
    """Ask the knowledge base a natural-language question (retrieval-augmented, cited)."""
    cfg, conn = _setup(config, repo, rev, db)
    provider = build_provider(cfg, conn)
    acfg = cfg.get("ask", {})
    kd = int(acfg.get("k_domains", 8))
    sem = [i for i, _ in semantic(conn, provider, question, k=kd)]
    dom_ids = list(dict.fromkeys(sem + keyword_domains(conn, question, k=kd)))[:kd]
    if not dom_ids:
        console.print("[yellow]No indexed domains — run `gitchronicle index` first.[/]")
        raise typer.Exit(1)
    blocks = []
    for did in dom_ids:
        d = conn.execute("SELECT id, name, classification, summary, n_commits, "
                         "first_seen, last_seen FROM domains WHERE id=?", (did,)).fetchone()
        if not d:
            continue
        authors = [r["author_name"] for r in conn.execute(
            "SELECT c.author_name FROM commit_domains cd JOIN commits c ON c.hash=cd.commit_hash "
            "WHERE cd.domain_id=? GROUP BY c.author_name ORDER BY COUNT(*) DESC LIMIT 4", (did,))]
        story = " ".join(r["narrative"] or "" for r in conn.execute(
            "SELECT narrative FROM evolution_chapters WHERE target_type='domain' AND target_id=? "
            "ORDER BY seq", (str(d["id"]),)))
        blocks.append(
            f"DOMAIN: {d['name']} [{d['classification']}] ({d['n_commits']} commits, "
            f"{(d['first_seen'] or '')[:10]}..{(d['last_seen'] or '')[:10]}; "
            f"authors: {', '.join(a for a in authors if a)})\n"
            f"  what: {d['summary'] or '(n/a)'}"
            + (f"\n  evolution: {story[:600]}" if story else ""))
    commits = []
    for h in keyword_commits(conn, question, k=int(acfg.get("k_commits", 10))):
        r = conn.execute("SELECT hash, authored_at, author_name, subject FROM commits WHERE hash=?",
                         (h,)).fetchone()
        if r:
            commits.append(f"  {r['hash'][:10]} {(r['authored_at'] or '')[:10]} "
                           f"{r['author_name']}: {r['subject']}")
    system = ("You answer questions about a software project using ONLY the knowledge base "
              "provided (domains and commits). Cite domains by name and commits by short hash. "
              "If the knowledge base does not cover it, say so. Be concise and concrete.")
    user = (f"QUESTION: {question}\n\nKNOWLEDGE BASE — DOMAINS:\n" + "\n\n".join(blocks)
            + "\n\nRELEVANT COMMITS:\n" + ("\n".join(commits) or "(none)")
            + "\n\nAnswer the question, citing domains and commit hashes.")
    ans = provider.chat(system, user, want_json=False, large=True)
    console.print(ans if isinstance(ans, str) else str(ans))


if __name__ == "__main__":
    app()
