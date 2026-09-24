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

from .chronicle import chronicle
from .untangle import untangle
from .config import load_config
from .enrich import enrich
from .extract import ingest
from .extract.git_ingest import run_git
from .llm import build_provider
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


@app.command(name="chronicle", hidden=True)
def chronicle_cmd(config: str = _Config, repo: str = _Repo, rev: str = _Rev, db: str = _Db,
                  force: bool = typer.Option(False, "--force", help="Rebuild all chapters")):
    """Build the narrative evolution chronicle (per domain + repo). LLM; opt-in."""
    cfg, conn = _setup(config, repo, rev, db)
    _head("Chronicle")
    provider = build_provider(cfg, conn)
    chronicle(conn, provider, cfg["repo"]["path"], log=_log, force=force)


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
                 title=Path(cfg["repo"]["path"]).name, repo=cfg["repo"]["path"],
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
    serve_mcp(kb, repo=cfg["repo"]["path"],
              scope_path=str(Path(config).resolve().parent / "gitchronicle.md"))


@app.command(name="inspect")
def inspect_cmd(query: str = typer.Argument(..., help="A path, a filename, or a word"),
                config: str = _Config, repo: str = _Repo, rev: str = _Rev, db: str = _Db,
                plain: bool = typer.Option(False, "--files", help="Just the paths, one per line")):
    """Who owns these files, what they were built for, and what belongs with them."""
    from .scope import Scope
    from .serve.inspect import file_card, find, split_hint
    cfg, _ = _setup(config, repo, rev, db)
    kb = cfg.get("output", {}).get("kb", cfg["db"]["path"])
    if not Path(kb).exists():
        console.print(f"[yellow]No knowledge base at {kb} — run `gitchronicle update`.[/]")
        raise typer.Exit(1)
    conn, repo_path, sc = connect(kb), cfg["repo"]["path"], Scope.load()
    found = find(conn, query, scope=sc)
    if plain:
        for f in found["files"]:
            console.print(f, markup=False, highlight=False)
        return
    if not found["files"]:
        console.print(f"[yellow]nothing matches {query!r}[/]")
        raise typer.Exit(1)
    if len(found["files"]) == 1 or "/" in query:
        card = file_card(conn, found["files"][0], repo_path)
        _head(card["path"])
        e = card["entry"]
        _log(f"  owned by: {e['name'] + ' [' + (e['tier'] or '') + ']' if e else '(no entry)'}")
        if card["chapters"]:
            _log("  told in: " + "; ".join(f"{c['title']} ({c['start'][:7]})" for c in card["chapters"][:4]))
        if card["concerns"]:
            _log("  built for:")
            for c in card["concerns"][:8]:
                _log(f"    {c['date']}  {c['label']}" + (f"   → {c['entry']}" if c["entry"] else ""))
        if card["cochanged"]:
            _log("  changes with:")
            for c in card["cochanged"][:6]:
                _log(f"    {c['n']:3}x  {c['path']}" + (f"   ({c['entry']})" if c["entry"] else ""))
        return
    _head(f"{found['total']} files match {query!r}")
    for grp in found["entries"]:
        _log(f"  {grp['n']:3}  {grp['name'] or '(no entry)'}")
        for f in grp["files"][:6]:
            _log(f"         {f}")
    hint = split_hint(conn, repo_path, query, scope=sc)
    if hint["core"] and hint["clients"]:
        _head("Framework, or its users?")
        _log("  these are included by the others — the framework:")
        for x in hint["core"][:8]:
            _log(f"    {x['path']}" + (f"   (included by {x['included_by']})" if x["included_by"] else ""))
        _log(f"  these {len(hint['clients'])} include it — its users:")
        for f in hint["clients"][:8]:
            _log(f"    {f}")
        _log("  claim the first group as one entry; the second belongs to whatever uses it.")


@app.command(name="doctor")
def doctor_cmd(config: str = _Config, repo: str = _Repo, rev: str = _Rev, db: str = _Db):
    """Check the setup: git, the repository, the knowledge base, and every model role."""
    from .llm.doctor import check_providers, check_scope
    cfg = load_config(config)
    if repo:
        cfg["repo"]["path"] = repo
    _head("Doctor")
    repo_path = Path(cfg["repo"]["path"])
    ok = True
    if not (repo_path / ".git").exists():
        _log(f"  ✗ repo: {repo_path} is not a git repository (set [repo].path)")
        ok = False
    else:
        head = run_git(str(repo_path), ["rev-parse", "--short", "HEAD"]).strip()
        n = run_git(str(repo_path), ["rev-list", "--count", cfg["repo"]["rev_range"]]).strip()
        _log(f"  ✓ repo: {repo_path} @ {head} · {n} commits in {cfg['repo']['rev_range']}")
    kb = cfg.get("output", {}).get("kb", "")
    _log(f"  {'✓' if kb and Path(kb).exists() else '·'} knowledge base: "
         f"{kb or '(unset)'}{'' if kb and Path(kb).exists() else ' — build it with `update`'}")
    check_scope(log=_log)
    res = check_providers(cfg, log=_log)
    if not res["ok"]:
        raise typer.Exit(1)
    if not ok:
        raise typer.Exit(1)


@app.command(name="cost")
def cost_cmd(config: str = _Config, repo: str = _Repo, rev: str = _Rev, db: str = _Db,
             since: str = typer.Option("", "--since", help="Only calls on/after this date"),
             estimate_commits: int = typer.Option(0, "--estimate",
                 help="Estimate untangling this many more commits")):
    """What the runs cost, per stage, from the cache's own token counts."""
    from .llm.cost import estimate as est, report
    cfg, conn = _setup(config, repo, rev, db)
    pricing = cfg.get("pricing", {})
    _head("Cost")
    report(conn, pricing, since=since, log=_log)
    if estimate_commits:
        _head("Estimate")
        est(conn, pricing, estimate_commits, log=_log)


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


tax_app = typer.Typer(add_completion=False, no_args_is_help=True,
                      help="Inspect and curate the feature taxonomy (optional review seam; "
                           "the pipeline never blocks on it).")


def _resolve_asof(conn, asof: Optional[str]) -> Optional[str]:
    if not asof:
        return None
    row = conn.execute("SELECT time_start FROM eras WHERE name=?", (asof,)).fetchone()
    return (row["time_start"][:10] if row else asof)


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
