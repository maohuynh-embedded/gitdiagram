"""archdiag command line: analyze | serve | doctor | bench."""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections.abc import Callable, Sequence
from pathlib import Path

from archdiag import __version__
from archdiag.bench import format_report, load_expected, score
from archdiag.config import USER_CONFIG, ConfigError, Settings, is_loopback, load_settings
from archdiag.llm.client import LLMError, LLMUnavailable, make_client
from archdiag.pipeline import AnalysisError, AnalysisResult, analyze
from archdiag.scan.snapshot import ScanError, build_snapshot

EXIT_OK = 0
EXIT_ANALYSIS = 1
EXIT_ENVIRONMENT = 2


def _add_llm_options(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--model", help="model name, e.g. qwen3-coder:30b")
    parser.add_argument("--endpoint", help="LLM runtime URL (default http://127.0.0.1:11434)")
    parser.add_argument("--api", choices=["ollama", "openai"], help="LLM API flavour")
    parser.add_argument("--language", help="language of the written explanation")
    parser.add_argument("--allow-remote-llm", action="store_true",
                        help="allow an LLM endpoint that is not on this machine")


def _add_repo_options(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("repo", type=Path, help="repository directory")
    parser.add_argument("--out", type=Path, help="output directory (default <repo>/.archdiag)")
    _add_llm_options(parser)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="archdiag", description=__doc__)
    parser.add_argument("--version", action="version", version=f"archdiag {__version__}")
    commands = parser.add_subparsers(dest="command", required=True)

    analyze_cmd = commands.add_parser("analyze", help="analyse a repository and write diagrams")
    _add_repo_options(analyze_cmd)
    analyze_cmd.add_argument("--no-cache", action="store_true", help="ignore a previous result")

    serve_cmd = commands.add_parser("serve", help="open the local web viewer")
    _add_repo_options(serve_cmd)
    serve_cmd.add_argument("--port", type=int, default=8765)

    doctor_cmd = commands.add_parser("doctor", help="check the local LLM setup")
    doctor_cmd.add_argument("repo", type=Path, nargs="?", help="repository (for its config)")
    _add_llm_options(doctor_cmd)

    bench_cmd = commands.add_parser("bench", help="score the result against an answer file")
    _add_repo_options(bench_cmd)
    bench_cmd.add_argument("--expected", type=Path, required=True)
    bench_cmd.add_argument("--no-cache", action="store_true")
    return parser


def _settings(args: argparse.Namespace, repo: Path | None) -> Settings:
    overrides = {
        "model": args.model, "endpoint": args.endpoint, "api": args.api, "language": args.language,
    }
    settings = load_settings(repo, overrides, user_config=USER_CONFIG)
    if not is_loopback(settings.endpoint) and not args.allow_remote_llm:
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


def _summarise(result: AnalysisResult) -> None:
    source = "cached result" if result.cached else f"{result.attempts} attempt(s)"
    print(f"{len(result.model.nodes)} nodes, {len(result.model.edges)} edges ({source})")
    for warning in result.warnings:
        print(f"warning: {warning}")
    print(f"written to {result.out_dir}")


def _cmd_analyze(args: argparse.Namespace) -> int:
    repo, out = _repo_and_out(args)
    settings = _settings(args, repo)
    result = analyze(repo, out, settings, make_client(settings),
                     use_cache=not args.no_cache, progress=_progress)
    _summarise(result)
    return EXIT_OK


def _cmd_serve(args: argparse.Namespace) -> int:
    from archdiag.web.server import serve

    repo, out = _repo_and_out(args)
    settings = _settings(args, repo)
    serve(repo, out, settings, lambda: make_client(settings), args.port)
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
    return EXIT_OK


def _cmd_bench(args: argparse.Namespace) -> int:
    repo, out = _repo_and_out(args)
    settings = _settings(args, repo)
    expected = load_expected(args.expected)
    result = analyze(repo, out, settings, make_client(settings),
                     use_cache=not args.no_cache, progress=_progress)
    snapshot = build_snapshot(repo, settings.include, settings.exclude)
    report = score(result.model, expected, snapshot.known_paths(),
                   [f.path for f in snapshot.files])
    (out / "bench.json").write_text(json.dumps(report.to_dict(), indent=2), encoding="utf-8")
    print(format_report(report))
    return EXIT_OK


_COMMANDS: dict[str, Callable[[argparse.Namespace], int]] = {
    "analyze": _cmd_analyze,
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
