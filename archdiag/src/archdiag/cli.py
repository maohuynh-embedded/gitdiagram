"""archdiag command line: analyze | check | serve | doctor | bench."""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections.abc import Callable, Sequence
from pathlib import Path

from archdiag import __version__
from archdiag.ad.pipeline import ALL_NOTATIONS
from archdiag.bench import BenchReport, format_report, load_expected, score
from archdiag.config import USER_CONFIG, ConfigError, Settings, is_loopback, load_settings
from archdiag.llm.client import ChatClient, LLMError, LLMUnavailable, make_client
from archdiag.pipeline import AnalysisError
from archdiag.run import MODES, RunOutcome, run_analysis
from archdiag.scan.snapshot import ScanError, build_snapshot

EXIT_OK = 0
EXIT_ANALYSIS = 1
EXIT_ENVIRONMENT = 2
EXIT_VIOLATIONS = 3


def _add_llm_options(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--model", help="model name, e.g. qwen3-coder:30b")
    parser.add_argument("--endpoint", help="LLM runtime URL (default http://127.0.0.1:11434)")
    parser.add_argument("--api", choices=["ollama", "openai"], help="LLM API flavour")
    parser.add_argument("--language", help="language of the written explanation")
    parser.add_argument(
        "--allow-remote-llm",
        action="store_true",
        help="allow an LLM endpoint that is not on this machine",
    )


def _add_analysis_options(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--mode",
        choices=MODES,
        default="auto",
        help="static analysis, LLM only, or auto (static when there is C/C++)",
    )
    parser.add_argument(
        "--no-llm",
        action="store_true",
        help="static analysis without annotation; never calls a model",
    )
    parser.add_argument(
        "--compile-db",
        type=Path,
        help="compile_commands.json for libclang (found automatically otherwise)",
    )
    parser.add_argument("--rebuild-facts", action="store_true", help="drop the facts cache")


def _add_repo_options(parser: argparse.ArgumentParser, llm: bool = True) -> None:
    parser.add_argument("repo", type=Path, help="repository directory")
    parser.add_argument("--out", type=Path, help="output directory (default <repo>/.archdiag)")
    if llm:
        _add_llm_options(parser)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="archdiag", description=__doc__)
    parser.add_argument("--version", action="version", version=f"archdiag {__version__}")
    commands = parser.add_subparsers(dest="command", required=True)

    analyze_cmd = commands.add_parser("analyze", help="analyse a repository and write diagrams")
    _add_repo_options(analyze_cmd)
    _add_analysis_options(analyze_cmd)
    analyze_cmd.add_argument("--no-cache", action="store_true", help="ignore a previous result")

    check_cmd = commands.add_parser(
        "check", help="static analysis and architecture rules only; exit 3 on violations"
    )
    _add_repo_options(check_cmd, llm=False)
    check_cmd.add_argument("--compile-db", type=Path)
    check_cmd.add_argument("--rebuild-facts", action="store_true")

    serve_cmd = commands.add_parser("serve", help="open the local web viewer")
    _add_repo_options(serve_cmd)
    _add_analysis_options(serve_cmd)
    serve_cmd.add_argument("--port", type=int, default=8765)

    doctor_cmd = commands.add_parser("doctor", help="check the local LLM setup")
    doctor_cmd.add_argument("repo", type=Path, nargs="?", help="repository (for its config)")
    _add_llm_options(doctor_cmd)

    ad_cmd = commands.add_parser(
        "ad", help="write the ISO 42010 architecture description with standard notations"
    )
    _add_repo_options(ad_cmd)
    ad_cmd.add_argument(
        "--notation",
        default=",".join(ALL_NOTATIONS),
        help=f"comma-separated subset of {','.join(ALL_NOTATIONS)}",
    )
    ad_cmd.add_argument("--no-llm", action="store_true", help="no annotation or overview text")
    ad_cmd.add_argument("--compile-db", type=Path)
    ad_cmd.add_argument(
        "--strict", action="store_true", help="exit 3 when a validator reports an error"
    )

    fetch_cmd = commands.add_parser(
        "fetch-schemas", help="download the ArchiMate exchange-format XSDs once"
    )
    fetch_cmd.add_argument("--force", action="store_true")

    bench_cmd = commands.add_parser("bench", help="score the result against an answer file")
    _add_repo_options(bench_cmd)
    _add_analysis_options(bench_cmd)
    bench_cmd.add_argument("--expected", type=Path, required=True)
    bench_cmd.add_argument("--no-cache", action="store_true")
    bench_cmd.add_argument(
        "--compare",
        action="store_true",
        help="run the LLM-only and the static path and compare them",
    )
    return parser


def _settings(args: argparse.Namespace, repo: Path | None) -> Settings:
    overrides = {
        "model": getattr(args, "model", None),
        "endpoint": getattr(args, "endpoint", None),
        "api": getattr(args, "api", None),
        "language": getattr(args, "language", None),
    }
    settings = load_settings(repo, overrides, user_config=USER_CONFIG)
    if not is_loopback(settings.endpoint) and not getattr(args, "allow_remote_llm", False):
        raise ConfigError(
            f"The LLM endpoint {settings.endpoint} is not on this machine, so your code would "
            "leave it. Pass --allow-remote-llm if that is intended."
        )
    return settings


def _repo_and_out(args: argparse.Namespace) -> tuple[Path, Path]:
    repo: Path = args.repo
    if not repo.is_dir():
        raise ScanError(f"{repo} is not a directory")
    out: Path = args.out if args.out else repo / ".archdiag"
    return repo, out


def _progress(step: str, message: str) -> None:
    print(f"[{step}] {message}", file=sys.stderr, flush=True)


def _client_factory(
    args: argparse.Namespace, settings: Settings
) -> Callable[[], ChatClient] | None:
    if getattr(args, "no_llm", False):
        return None
    return lambda: make_client(settings)


def _run(
    args: argparse.Namespace,
    repo: Path,
    out: Path,
    settings: Settings,
    mode: str | None = None,
    use_cache: bool = True,
) -> RunOutcome:
    return run_analysis(
        repo,
        out,
        settings,
        client_factory=_client_factory(args, settings),
        mode=mode or str(getattr(args, "mode", "auto")),
        compile_db=getattr(args, "compile_db", None),
        rebuild=getattr(args, "rebuild_facts", False),
        use_cache=use_cache,
        progress=_progress,
    )


def _summarise(outcome: RunOutcome) -> None:
    model = outcome.model
    print(f"{outcome.mode}: {len(model.nodes)} nodes, {len(model.edges)} edges ({outcome.detail})")
    for warning in outcome.warnings:
        print(f"warning: {warning}")
    print(f"written to {outcome.out_dir}")


def _cmd_analyze(args: argparse.Namespace) -> int:
    repo, out = _repo_and_out(args)
    settings = _settings(args, repo)
    _summarise(_run(args, repo, out, settings, use_cache=not args.no_cache))
    return EXIT_OK


def _cmd_check(args: argparse.Namespace) -> int:
    repo, out = _repo_and_out(args)
    settings = _settings(args, repo)
    args.no_llm = True
    outcome = _run(args, repo, out, settings, mode="static")
    names = {n.id: n.label for n in outcome.model.nodes}
    for violation in outcome.model.violations:
        where = ", ".join(f"{e.file}:{e.line}" for e in violation.evidence[:3])
        target = f" -> {names.get(violation.target, violation.target)}" if violation.target else ""
        print(
            f"{violation.rule}: {names.get(violation.source, violation.source)}{target}: "
            f"{violation.message} [{where}]"
        )
    count = len(outcome.model.violations)
    print(f"{count} violation(s)")
    return EXIT_VIOLATIONS if count else EXIT_OK


def _cmd_serve(args: argparse.Namespace) -> int:
    from archdiag.web.server import serve

    repo, out = _repo_and_out(args)
    settings = _settings(args, repo)

    def runner(progress: Callable[[str, str], None]) -> RunOutcome:
        return run_analysis(
            repo,
            out,
            settings,
            client_factory=_client_factory(args, settings),
            mode=args.mode,
            compile_db=args.compile_db,
            rebuild=False,
            use_cache=False,
            progress=progress,
        )

    serve(repo, out, runner, args.port)
    return EXIT_OK


def _total_ram_bytes() -> int:
    try:
        return os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_PHYS_PAGES")
    except (ValueError, OSError):
        return 0


def _cmd_doctor(args: argparse.Namespace) -> int:
    settings = _settings(args, args.repo)
    print(f"LLM runtime: {settings.api} at {settings.endpoint}")
    try:
        models = make_client(settings).list_models()
    except LLMError as exc:
        print(f"  ✘ {exc}")
        return EXIT_ENVIRONMENT
    print(f"  ✔ reachable, {len(models)} model(s) installed")
    wanted = settings.model
    size = models.get(wanted, models.get(f"{wanted}:latest"))
    if size is None:
        print(f"  ✘ model {wanted} is not installed. Run: ollama pull {wanted}")
        return EXIT_ENVIRONMENT
    print(f"  ✔ model {wanted} is available")
    ram = _total_ram_bytes()
    if size and ram:
        print(f"  model size {size / 1e9:.1f} GB, machine RAM {ram / 1e9:.1f} GB")
        if size > ram * 0.75:
            print("  ! the model uses most of the RAM; pick a smaller one if runs are slow")
    try:
        from archdiag.facts.extract_clang import clang_setup

        setup = clang_setup()
    except ImportError:
        setup = None
    print(
        "libclang: "
        + (
            "available" + (f" ({setup.library})" if setup and setup.library else "")
            if setup
            else "not installed (optional: pip install 'archdiag[clang]')"
        )
    )
    return EXIT_OK


def _score(
    outcome: RunOutcome, args: argparse.Namespace, repo: Path, settings: Settings
) -> BenchReport:
    snapshot = build_snapshot(repo, settings.include, settings.exclude)
    return score(
        outcome.model,
        load_expected(args.expected),
        snapshot.known_paths(),
        [f.path for f in snapshot.files],
    )


def _cmd_bench(args: argparse.Namespace) -> int:
    repo, out = _repo_and_out(args)
    settings = _settings(args, repo)
    load_expected(args.expected)  # fail fast on a bad answer file
    if not args.compare:
        outcome = _run(args, repo, out, settings, use_cache=not args.no_cache)
        report = _score(outcome, args, repo, settings)
        (out / "bench.json").write_text(json.dumps(report.to_dict(), indent=2), encoding="utf-8")
        print(format_report(report))
        return EXIT_OK
    reports: dict[str, BenchReport] = {}
    for mode in ("llm", "static"):
        outcome = _run(args, repo, out / mode, settings, mode=mode, use_cache=not args.no_cache)
        reports[mode] = _score(outcome, args, repo, settings)
        (out / mode / "bench.json").write_text(
            json.dumps(reports[mode].to_dict(), indent=2), encoding="utf-8"
        )
    rows = [
        ("component recall", "component_recall"),
        ("relation recall", "relation_recall"),
        ("path validity", "path_validity"),
    ]
    print(f"{'':18}{'llm':>10}{'static':>10}")
    for label, attr in rows:
        print(
            f"{label:18}{getattr(reports['llm'], attr):>10.1%}"
            f"{getattr(reports['static'], attr):>10.1%}"
        )
    print(
        f"{'junk nodes':18}{len(reports['llm'].junk_nodes):>10}"
        f"{len(reports['static'].junk_nodes):>10}"
    )
    return EXIT_OK


def _cmd_ad(args: argparse.Namespace) -> int:
    from archdiag.ad.pipeline import generate_ad

    repo, out = _repo_and_out(args)
    settings = _settings(args, repo)
    notations = [n.strip() for n in args.notation.split(",") if n.strip()]
    result = generate_ad(
        repo,
        out,
        settings,
        notations=notations,
        client_factory=_client_factory(args, settings),
        compile_db=args.compile_db,
        progress=_progress,
    )
    for name, export in result.results.items():
        for issue in export.issues:
            where = f" ({issue.element})" if issue.element else ""
            print(f"{name}: {issue.severity}: {issue.message}{where}")
    for correspondence in result.ad.correspondences:
        if correspondence.status == "violated":
            print(f"correspondence {correspondence.rule_id}: {correspondence.detail}")
    print(
        f"{len(result.ad.views)} views, {result.errors} error(s), {result.warnings} "
        f"warning(s); written to {result.out_dir}"
    )
    return EXIT_VIOLATIONS if args.strict and result.errors else EXIT_OK


def _cmd_fetch_schemas(args: argparse.Namespace) -> int:
    from archdiag.ad.export.archimate import fetch_schemas

    for line in fetch_schemas(force=args.force):
        print(line)
    return EXIT_OK


_COMMANDS: dict[str, Callable[[argparse.Namespace], int]] = {
    "ad": _cmd_ad,
    "fetch-schemas": _cmd_fetch_schemas,
    "analyze": _cmd_analyze,
    "check": _cmd_check,
    "serve": _cmd_serve,
    "doctor": _cmd_doctor,
    "bench": _cmd_bench,
}


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        return _COMMANDS[args.command](args)
    except (ConfigError, ScanError, LLMUnavailable) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_ENVIRONMENT
    except (AnalysisError, LLMError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_ANALYSIS
    except KeyboardInterrupt:
        print("interrupted", file=sys.stderr)
        return EXIT_ANALYSIS


if __name__ == "__main__":
    sys.exit(main())
