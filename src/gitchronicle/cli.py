"""gitchronicle command-line interface (domain-centric).

Pipeline: extract -> signals -> untangle -> catalog -> attribute -> lifecycle ->
index -> graph. Every stage is resumable. Query the result with `domains` / `show`.
Config from config.toml; --repo/--rev/--db override it.
"""

from __future__ import annotations

import json as _json
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
@app.command(hidden=True)
def extract(config: str = _Config, repo: str = _Repo, rev: str = _Rev, db: str = _Db):
    """Ingest commits, file churn, branches and tags for the rev-range."""
    cfg, conn = _setup(config, repo, rev, db)
    _head("Extract")
    ingest(conn, cfg["repo"]["path"], cfg["repo"]["rev_range"], log=_log)


@app.command(hidden=True)
def signals(config: str = _Config, repo: str = _Repo, rev: str = _Rev, db: str = _Db):
    """Enrich commits: language, conventional-commit, work-kind, components."""
    cfg, conn = _setup(config, repo, rev, db)
    _head("Signals")
    enrich(conn, log=_log)


@app.command(name="untangle", hidden=True)
def untangle_cmd(config: str = _Config, repo: str = _Repo, rev: str = _Rev, db: str = _Db,
                 force: bool = typer.Option(False, "--force", help="Re-untangle all commits")):
    """Untangle each commit's diff into semantic concerns (LLM; message untrusted)."""
    cfg, conn = _setup(config, repo, rev, db)
    _head("Untangle")
    provider = build_provider(cfg, conn)
    untangle(conn, provider, cfg["repo"]["path"], log=_log, force=force,
             **_untangle_kwargs(cfg))


@app.command(hidden=True)
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


app.command(name="catalog", hidden=True)(catalog_cmd)


@app.command(hidden=True)
def attribute_cmd(config: str = _Config, repo: str = _Repo, rev: str = _Rev, db: str = _Db):
    """Attach commits to domains (many-to-many) by the files they touch."""
    _, conn = _setup(config, repo, rev, db)
    _head("Attribute")
    attribute(conn, log=_log)


app.command(name="attribute", hidden=True)(attribute_cmd)


@app.command(hidden=True)
def lifecycle_cmd(config: str = _Config, repo: str = _Repo, rev: str = _Rev, db: str = _Db):
    """Detect domain lifecycle (active/dormant/merged/removed) vs the ref tip."""
    cfg, conn = _setup(config, repo, rev, db)
    _head("Lifecycle")
    head = run_git(cfg["repo"]["path"], ["rev-parse", "HEAD"]).strip()
    lifecycle(conn, cfg["repo"]["path"], head, cfg, log=_log)


app.command(name="lifecycle", hidden=True)(lifecycle_cmd)


@app.command(hidden=True)
def discover_cmd(config: str = _Config, repo: str = _Repo, rev: str = _Rev, db: str = _Db,
                 force: bool = typer.Option(False, "--force", help="Re-name already-named domains"),
                 limit: Optional[int] = typer.Option(None, "--limit", help="Only the N largest domains")):
    """Name domains and infer what/why/evolution with the LLM (from diffs)."""
    cfg, conn = _setup(config, repo, rev, db)
    _head("Discover")
    provider = build_provider(cfg, conn)
    discover(conn, provider, cfg, cfg["repo"]["path"], log=_log, force=force, limit=limit)


app.command(name="discover", hidden=True)(discover_cmd)


@app.command(hidden=True)
def graph(config: str = _Config, repo: str = _Repo, rev: str = _Rev, db: str = _Db,
          out: Optional[str] = typer.Option(None, "--out", help="HTML output path"),
          json_out: Optional[str] = typer.Option(None, "--json", help="features.json output path")):
    """Export the self-contained HTML domain graph and JSON."""
    cfg, conn = _setup(config, repo, rev, db)
    _head("Graph")
    export_graph(conn, out or cfg["output"]["html"], json_out or cfg["output"]["json"], log=_log)


@app.command(name="chronicle", hidden=True)
def chronicle_cmd(config: str = _Config, repo: str = _Repo, rev: str = _Rev, db: str = _Db,
                  force: bool = typer.Option(False, "--force", help="Rebuild all chapters")):
    """Build the narrative evolution chronicle (per domain + repo). LLM; opt-in."""
    cfg, conn = _setup(config, repo, rev, db)
    _head("Chronicle")
    provider = build_provider(cfg, conn)
    chronicle(conn, provider, cfg["repo"]["path"], log=_log, force=force)


@app.command(hidden=True)
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
        "SELECT COUNT(*) FROM concerns WHERE domain_id IS NULL AND label IS NOT NULL AND (origin IS NULL OR origin != 'import-misc')").fetchone()[0]
    if changes:
        console.print("\n" + render_changeset(changes), markup=False, highlight=False)
    if unassigned:
        console.print(f"{unassigned} concerns fit no existing feature"
                      + (" (frozen: left unassigned)" if frozen else ""))
    if frozen and (changes or unassigned):
        raise typer.Exit(2)   # terraform-style: 0 = clean, 2 = changes awaiting review


@app.command(name="update")
def update_cmd(config: str = _Config, repo: str = _Repo, rev: str = _Rev, db: str = _Db,
               emit: Optional[str] = typer.Option(None, "--emit",
                   help="The knowledge base to (re)build (default: [output].kb in config)"),
               out: Optional[str] = typer.Option(None, "--out",
                   help="Dossier/kb.html directory (default: [output].kb_dir in config)"),
               chronicle_flag: bool = typer.Option(False, "--chronicle",
                   help="Narrate entries whose story is not cached yet (LLM spend)")):
    """Follow the repository: ingest what is new and rebuild the knowledge base.

    The command to run on a schedule. Ingest and untangle are already incremental — only
    commits with no concerns yet cost anything — and the assembly is deterministic and
    cheap, so it simply re-runs. Curation lives in the ledger as rules over paths, so a
    rebuild cannot disturb it; that is the whole reason the ledger is not keyed to
    anything the assembly generates.
    """
    from .taxonomy.ledger import Ledger
    from .taxonomy.lineage import build_lineage, emit_register
    from .taxonomy.relations import build_relations
    from .taxonomy.tiers import assign_tiers
    cfg, conn = _setup(config, repo, rev, db)
    repo_path, rev_range = cfg["repo"]["path"], cfg["repo"]["rev_range"]
    head = run_git(repo_path, ["rev-parse", "HEAD"]).strip()
    # paths belong in config, not in every invocation: this is the command meant to be
    # run on a schedule, and one that needs three flags every time will not be
    emit = emit or cfg.get("output", {}).get("kb", "gitchronicle-kb.db")
    out = out or cfg.get("output", {}).get("kb_dir", "kb")

    before = _catalogue_snapshot(emit)
    known = conn.execute("SELECT COUNT(*) FROM commits").fetchone()[0]
    console.print(f"[bold]gitchronicle update[/] · {repo_path} @ {head[:10]}")

    _head("Extract"); ingest(conn, repo_path, rev_range, log=_log)
    _head("Signals"); enrich(conn, log=_log)
    new = conn.execute("SELECT COUNT(*) FROM commits").fetchone()[0] - known
    provider = build_provider(cfg, conn)
    _head("Untangle"); untangle(conn, provider, repo_path, log=_log, **_untangle_kwargs(cfg))
    _head("Lineage")
    res = build_lineage(conn, repo_path, log=_log,
                        golden_probes=cfg.get("lineage", {}).get("golden"),
                        canonical=cfg.get("lineage", {}).get("canonical_paths", True))
    if not res["report"]["pass"]:
        console.print("[red]structural validation failed — knowledge base not rebuilt[/]")
        raise typer.Exit(2)
    emit_register(conn, repo_path, res, emit, provider, log=_log)
    out_conn = connect(emit); init_db(out_conn)
    _head("Relations"); build_relations(out_conn, repo_path, log=_log); out_conn.commit()
    _head("Tiers"); assign_tiers(out_conn, Ledger.load(), log=_log)
    # Chapters live in the KB, which is rebuilt every run — so the LLM cache must NOT.
    # `provider` caches against the working DB, which persists, so every chapter already
    # paid for comes back for free; without --chronicle nothing new is bought.
    _head("Chronicle")
    chronicle(out_conn, provider, repo_path, log=_log, cached_only=not chronicle_flag)
    out_conn.close()
    _head("Export")
    from .serve.dossier import export_dossiers
    export_dossiers(connect(emit), out, log=_log)

    _head("Since last update")
    after = _catalogue_snapshot(emit)
    for line in _catalogue_delta(before, after, new):
        _log("  " + line)


def _catalogue_snapshot(db_path: str) -> dict:
    """name -> territory size, or empty when there is no knowledge base yet."""
    if not Path(db_path).exists():
        return {}
    c = connect(db_path)
    try:
        return {r[0]: r[1] for r in c.execute(
            "SELECT d.name, COUNT(df.path) FROM domains d "
            "LEFT JOIN domain_files df ON df.domain_id = d.id GROUP BY d.id")}
    except Exception:
        return {}
    finally:
        c.close()


def _catalogue_delta(before: dict, after: dict, new_commits: int) -> list[str]:
    """What actually changed — the point of running on a schedule is seeing this."""
    if not before:
        return [f"{new_commits} commits ingested; {len(after)} entries (first build)"]
    added = sorted(set(after) - set(before))
    gone = sorted(set(before) - set(after))
    grew = sorted((n for n in set(after) & set(before) if after[n] > before[n]),
                  key=lambda n: before[n] - after[n])
    out = [f"{new_commits} new commits; {len(after)} entries "
           f"({len(added)} new, {len(gone)} gone, {len(grew)} grew)"]
    for n in added[:10]:
        out.append(f"  + {n} ({after[n]} files)")
    for n in gone[:10]:
        out.append(f"  - {n}")
    for n in grew[:10]:
        out.append(f"  ~ {n}: {before[n]} -> {after[n]} files")
    return out


@app.command(name="init")
def init_cmd(config: str = _Config, repo: str = _Repo, rev: str = _Rev, db: str = _Db,
             yes: bool = typer.Option(False, "--yes",
                 help="Accept both drafts without review stops (automation)"),
             scope_arg: Optional[str] = typer.Option(None, "--scope",
                 help="Path to a pre-reviewed gitchronicle.md"),
             ):
    """Bootstrap the analysis: draft the SCOPE map (what is the product) and stop for
    review. Never runs the pipeline, never spends on LLM calls. --yes accepts the draft."""
    from .scope import MD_FILE, Scope, draft_scope
    cfg, conn = _setup(config, repo, rev, db)
    repo_path = cfg["repo"]["path"]
    md = Path(MD_FILE)

    if scope_arg:
        src = Path(scope_arg)
        if src.exists() and src.resolve() != md.resolve():
            md.write_text(src.read_text(encoding="utf-8"), encoding="utf-8")
            console.print(f"scope taken from {scope_arg}")
    if not Scope.load(md).exists():
        _head("Init · scope draft")
        draft_scope(repo_path, md)
        console.print(f"drafted [bold]{md}[/] ## Scope "
                      f"({len(Scope.load(md).excludes)} suggested exclusions, evidence in "
                      f"comments)")
        if not yes:
            console.print("review the Scope section (flip any wrong verdict), then re-run "
                          "[bold]gitchronicle init[/]")
            raise typer.Exit()

    console.print("[green]init complete[/] — scope in place. Next: [bold]gitchronicle register[/]")


@app.command(name="register", hidden=True)
def register_cmd(config: str = _Config, repo: str = _Repo, rev: str = _Rev, db: str = _Db,
                 force: bool = typer.Option(False, "--force", help="Rebuild the register")):
    """Build the feature register from the CURRENT worktree (scoped): module units ->
    code-peek labels -> territory merge. History never decides identity."""
    from .taxonomy.register import build_register
    cfg, conn = _setup(config, repo, rev, db)
    _head("Register")
    provider = build_provider(cfg, conn)
    build_register(conn, provider, cfg["repo"]["path"], cfg, log=_log, force=force)


@app.command(name="lineage", hidden=True)
def lineage_cmd(config: str = _Config, repo: str = _Repo, rev: str = _Rev, db: str = _Db,
                emit: str = typer.Option(None, "--emit",
                                         help="Write the named v0.3 register to this fresh DB")):
    """v0.3 (experimental): history-native register — cluster untangled concerns on
    shared file lineage + coined stems. No worktree carving, no config, no LLM until
    --emit (naming pass). Prints the structural validation verdict."""
    from .taxonomy.lineage import build_lineage
    cfg, conn = _setup(config, repo, rev, db)
    _head("Lineage")
    res = build_lineage(conn, cfg["repo"]["path"], log=_log,
                        golden_probes=cfg.get("lineage", {}).get("golden"),
                        canonical=cfg.get("lineage", {}).get("canonical_paths", True))
    if not emit:
        return
    if not res["report"]["pass"]:
        _log("  --emit refused: structural validation did not pass")
        return
    from .taxonomy.lineage import emit_register
    provider = build_provider(cfg, conn)
    emit_register(conn, cfg["repo"]["path"], res, emit, provider, log=_log)

    # the register alone is a list; relations and tiers are what make it a map, and both
    # are deterministic and free, so there is no reason to make them a separate step
    from .taxonomy.ledger import Ledger
    from .taxonomy.relations import build_relations
    from .taxonomy.tiers import assign_tiers
    out = connect(emit)
    init_db(out)
    build_relations(out, cfg["repo"]["path"], log=_log)
    out.commit()
    assign_tiers(out, Ledger.load(), log=_log)
    out.close()


@app.command(name="studio")
def studio_cmd(config: str = _Config, repo: str = _Repo, rev: str = _Rev, db: str = _Db,
               port: int = typer.Option(8765, "--port", help="Localhost port")):
    """Browse the catalogue and edit the ledger in a browser.

    Leads with the diagnostic a terminal cannot show: which directories are split across
    many entries. A tree with no majority owner is usually one thing the assembly never
    saw as one. Claim it from the table, preview what moves, save.

    Reads the knowledge base and writes only `gitchronicle.plan` — re-run `update` to
    rebuild with it.
    """
    import sys

    from .serve.studio import serve_studio
    cfg, _ = _setup(config, repo, rev, db)
    _head("Studio")

    def provider_factory():
        # answers cache against the working DB like every other call, so asking the
        # same question twice is free
        return build_provider(cfg, connect(cfg["db"]["path"]))

    # the KB, not the working DB: the studio reads what `update` emits
    serve_studio(db or cfg.get("output", {}).get("kb", cfg["db"]["path"]),
                 log=_log, port=port, provider_factory=provider_factory,
                 title=Path(cfg["repo"]["path"]).name,
                 rebuild_argv=[sys.executable, "-m", "gitchronicle", "update",
                               "--config", config])


@app.command(name="mcp")
def mcp_cmd(config: str = _Config,
            kb: Optional[str] = typer.Option(None, "--kb", help="Knowledge base (default: [output].kb)")):
    """Serve the knowledge base to coding agents over MCP (stdio).

    Register it once, e.g. for Claude Code:
        claude mcp add chronicle -- gitchronicle mcp --config /abs/path/config.toml
    Read-only; stdout carries the protocol, so nothing else is printed.
    """
    from .serve.mcp import serve_mcp
    if kb is None:
        cfg = load_config(config)
        kb = cfg.get("output", {}).get("kb", cfg["db"]["path"])
        # agents start servers from anywhere: a relative path means relative to the config
        if not Path(kb).is_absolute():
            kb = str(Path(config).resolve().parent / kb)
    if not Path(kb).exists():
        sys.stderr.write(f"gitchronicle mcp: no knowledge base at {kb} — run `gitchronicle update`\n")
        raise typer.Exit(1)
    serve_mcp(kb)


@app.command(name="eval")
def eval_cmd(questions: str = typer.Argument(..., help="JSON list of {q, facts, unanswerable}"),
             samples: int = typer.Option(3, "--samples", help="Ask each question this many times"),
             config: str = _Config, repo: str = _Repo, rev: str = _Rev, db: str = _Db):
    """Grade the knowledge base: ask questions whose answers you know, score the answers."""
    from .evaluate import run_eval
    cfg, conn = _setup(config, repo, rev, db)
    kb = cfg.get("output", {}).get("kb", cfg["db"]["path"])
    _head("Eval")
    res = run_eval(questions, kb, build_provider(cfg, conn), log=_log, samples=samples)
    out = Path(questions).with_suffix(".result.json")
    out.write_text(_json.dumps(res, indent=1, ensure_ascii=False), encoding="utf-8")
    _log(f"  answers and verdicts -> {out}")


@app.command(name="ledger")
def ledger_cmd(config: str = _Config, repo: str = _Repo, rev: str = _Rev, db: str = _Db,
               edit: bool = typer.Option(False, "--edit", help="Open the ledger in $EDITOR"),
               draft: bool = typer.Option(False, "--draft",
                   help="Seed a ledger from the current catalogue (tiers + coined claims)")):
    """The curation ledger: entries defined by claim/reject rules over paths.

    Nothing here references a generated id, so a curation survives re-runs and history
    growth — which is what every earlier review mechanism failed to do. Replay is free;
    edit the file and re-run `lineage --emit` to see it applied.
    """
    import subprocess as sp
    from .taxonomy.ledger import PLAN_FILE, Ledger
    cfg, conn = _setup(config, repo, rev, db)
    _head("Ledger")
    path = Path(PLAN_FILE)

    if draft:
        if path.exists():
            _log(f"  {path} exists — edit it rather than overwriting a curation")
            raise typer.Exit(1)
        led = Ledger()
        from .taxonomy.ledger import Entry
        for r in conn.execute(
                "SELECT name, tier, stems FROM domains WHERE tier IN ('foundation','framework') "
                "ORDER BY name"):
            e = Entry(r["name"])
            e.tier = r["tier"]
            for s in _json.loads(r["stems"] or "[]"):
                # bigram stems ('auto hunt') identify a family but can never match a
                # path; only single tokens make a usable claim
                if s and " " not in s:
                    e.claims.append(f"**/{s}*")
            led.entries.append(e)
        led.save(path)
        _log(f"  drafted {len(led.entries)} entries -> {path}  (review before trusting)")
        return

    led = Ledger.load(path)
    if edit:
        if not path.exists():
            path.write_text(Ledger().render(), encoding="utf-8")
        editor = os.environ.get("EDITOR") or os.environ.get("VISUAL")
        if not editor or not sys.stdin.isatty():
            _log(f"  no $EDITOR — edit {path} directly")
            return
        sp.call([editor, str(path)])
        led = Ledger.load(path)

    if not led.exists():
        _log(f"  no ledger yet. `gitchronicle ledger --draft` seeds one from the catalogue.")
        return
    for e in led.entries:
        _log(f"  {e.name}   tier={e.tier or '-'}  "
             f"claims={len(e.claims)} rejects={len(e.rejects)}{'  [locked]' if e.locked else ''}")
    for s, d in led.merges:
        _log(f"  merge  {s} -> {d}")
    for t in led.tombstones:
        _log(f"  reject {t}")


@app.command(hidden=True)
def dossier(config: str = _Config, repo: str = _Repo, rev: str = _Rev, db: str = _Db,
            out: str = typer.Option("dossiers", "--out", help="Output directory")):
    """Export per-feature dossier bundles (md+json with commit citations) + journey index."""
    from .serve.dossier import export_dossiers
    cfg, conn = _setup(config, repo, rev, db)
    _head("Dossiers")
    export_dossiers(conn, out, log=_log)


@app.command(hidden=True)
def relations(config: str = _Config, repo: str = _Repo, rev: str = _Rev, db: str = _Db):
    """Infer built-on/uses edges between register features from static imports (local)."""
    from .taxonomy.relations import build_relations
    cfg, conn = _setup(config, repo, rev, db)
    _head("Relations")
    build_relations(conn, cfg["repo"]["path"], log=_log)


@app.command(hidden=True)
def check(config: str = _Config, repo: str = _Repo, rev: str = _Rev, db: str = _Db):
    """Taxonomy health metrics: stem coherence, near-dups, confidence, coverage."""
    from .taxonomy.check import print_health
    _, conn = _setup(config, repo, rev, db)
    _head("Check")
    print_health(conn, log=_log)


@app.command(hidden=True)
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
app.add_typer(tax_app, name="taxonomy", hidden=True)


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


@app.command(hidden=True)
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


@app.command(hidden=True)
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


@app.command(hidden=True)
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


@app.command(name="index", hidden=True)
def index_cmd(config: str = _Config, repo: str = _Repo, rev: str = _Rev, db: str = _Db):
    """Build the semantic + full-text search indexes over the knowledge base."""
    cfg, conn = _setup(config, repo, rev, db)
    _head("Index")
    provider = build_provider(cfg, conn)
    build_index(conn, provider, log=_log)


@app.command(hidden=True)
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


@app.command(hidden=True)
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
    """Ask the knowledge base a question; the answer cites entries and commits."""
    from .serve.search import Index, ask as kb_ask
    cfg, conn = _setup(config, repo, rev, db)
    kb = cfg.get("output", {}).get("kb", cfg["db"]["path"])
    if not Path(kb).exists():
        console.print(f"[yellow]No knowledge base at {kb} — run `gitchronicle update` first.[/]")
        raise typer.Exit(1)
    r = kb_ask(Index(kb), kb, build_provider(cfg, conn), question)
    console.print(r["answer"])
    if r["used"]:
        console.print(f"\n[dim]read: {', '.join(r['used'])}[/]")


if __name__ == "__main__":
    app()
