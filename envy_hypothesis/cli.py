"""Command-line entry point. The Hypothesis window is the default.

Score and matrix reads are read-only; every mutation (adding a hypothesis or
evidence, rating a cell, removing an item) writes a brand-new analysis revision
and never overwrites the previous one.
"""

from __future__ import annotations

import argparse
import getpass
import json
import sys

from . import __version__
from .core import (HypothesisError, add_evidence, add_hypothesis, check_passphrase, create,
                   export, inspect, matrix, rate, remove_evidence, remove_hypothesis,
                   reweight, score)


def build_parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(
        prog="envy-hypothesis",
        description="Analysis of Competing Hypotheses — structure a judgement instead of guessing it. "
                    "List competing hypotheses and evidence, rate how consistent each piece of evidence is "
                    "with each hypothesis, and see which hypothesis is least contradicted and which evidence "
                    "actually discriminates. Run with no arguments to open the window. Local only: no network, "
                    "no accounts.")
    root.add_argument("--version", action="version", version=f"envy-hypothesis {__version__}")
    commands = root.add_subparsers(dest="command")
    commands.add_parser("gui", help="open the Hypothesis window (default)")

    new = commands.add_parser("init", help="create a new, empty encrypted analysis")
    new.add_argument("destination", help="a new analysis file; never overwritten")
    new.add_argument("--title", required=True, help="a name for this analysis")
    new.add_argument("--question", default="", help="the key question being analysed")

    ah = commands.add_parser("hypothesis", help="add a competing hypothesis (writes a new revision)")
    ah.add_argument("analysis")
    ah.add_argument("destination", help="a new analysis file; never overwritten")
    ah.add_argument("--label", required=True, help="the hypothesis, stated plainly")

    ae = commands.add_parser("evidence", help="add an evidence item (writes a new revision)")
    ae.add_argument("analysis")
    ae.add_argument("destination", help="a new analysis file; never overwritten")
    ae.add_argument("--text", required=True, help="the evidence or argument")
    ae.add_argument("--source", default="", help="an optional source note")
    ae.add_argument("--credibility", type=int, default=3, metavar="1-5",
                    help="how much weight it carries, 1 (weak) to 5 (strong); default 3")

    rt = commands.add_parser("rate", help="rate one evidence/hypothesis cell (writes a new revision)")
    rt.add_argument("analysis")
    rt.add_argument("destination", help="a new analysis file; never overwritten")
    rt.add_argument("--evidence", required=True, help="the evidence id")
    rt.add_argument("--hypothesis", required=True, help="the hypothesis id")
    rt.add_argument("--value", required=True,
                    help="very-consistent | consistent | neutral | inconsistent | very-inconsistent "
                         "(or -2..2, or NA to clear)")

    rw = commands.add_parser("reweight", help="change an evidence item's credibility (writes a new revision)")
    rw.add_argument("analysis")
    rw.add_argument("destination", help="a new analysis file; never overwritten")
    rw.add_argument("--evidence", required=True, help="the evidence id")
    rw.add_argument("--credibility", type=int, required=True, metavar="1-5")

    rmh = commands.add_parser("remove-hypothesis", help="remove a hypothesis and its ratings")
    rmh.add_argument("analysis")
    rmh.add_argument("destination", help="a new analysis file; never overwritten")
    rmh.add_argument("--hypothesis", required=True)

    rme = commands.add_parser("remove-evidence", help="remove an evidence item")
    rme.add_argument("analysis")
    rme.add_argument("destination", help="a new analysis file; never overwritten")
    rme.add_argument("--evidence", required=True)

    sc = commands.add_parser("score", help="rank hypotheses by weighted inconsistency (read-only)")
    sc.add_argument("analysis")

    mx = commands.add_parser("matrix", help="print the full ACH grid (read-only)")
    mx.add_argument("analysis")

    insp = commands.add_parser("inspect", help="print the decrypted analysis and journal (read-only)")
    insp.add_argument("analysis")

    exp = commands.add_parser("export", help="write a plaintext HTML or JSON report")
    exp.add_argument("analysis")
    exp.add_argument("destination", help="a new report file; never overwritten")
    exp.add_argument("--format", default="html", choices=("html", "json", "csv"))
    return root


def _passphrase(confirm: bool = False) -> bytes:
    if not sys.stdin.isatty():
        raise HypothesisError("Open this action in a terminal so the passphrase is not echoed")
    value = getpass.getpass("Analysis passphrase: ").encode()
    if confirm and value != getpass.getpass("Repeat passphrase: ").encode():
        raise HypothesisError("Passphrases do not match")
    return check_passphrase(value)


def _run(args) -> int:
    if args.command == "init":
        result = create(args.destination, args.title, _passphrase(confirm=True), args.question)
        print(f"analysis created → {result['output']} (revision 0)")
        return 0
    if args.command == "hypothesis":
        result = add_hypothesis(args.analysis, args.destination, _passphrase(), args.label)
        print(f"hypothesis added {result['hypothesis_id']} → {result['output']} (revision {result['revision']})")
        return 0
    if args.command == "evidence":
        result = add_evidence(args.analysis, args.destination, _passphrase(), args.text,
                              args.source, args.credibility)
        print(f"evidence added {result['evidence_id']} → {result['output']} (revision {result['revision']})")
        return 0
    if args.command == "rate":
        result = rate(args.analysis, args.destination, _passphrase(), args.evidence,
                      args.hypothesis, args.value)
        state = "cleared" if result["rating"] is None else f"rated {result['rating']}"
        print(f"cell {state} → {result['output']} (revision {result['revision']})")
        return 0
    if args.command == "reweight":
        result = reweight(args.analysis, args.destination, _passphrase(), args.evidence, args.credibility)
        print(f"reweighted {result['evidence_id']} → {result['output']} (revision {result['revision']})")
        return 0
    if args.command == "remove-hypothesis":
        result = remove_hypothesis(args.analysis, args.destination, _passphrase(), args.hypothesis)
        print(f"removed hypothesis {result['removed']} → {result['output']} (revision {result['revision']})")
        return 0
    if args.command == "remove-evidence":
        result = remove_evidence(args.analysis, args.destination, _passphrase(), args.evidence)
        print(f"removed evidence {result['removed']} → {result['output']} (revision {result['revision']})")
        return 0
    if args.command == "score":
        rep = score(args.analysis, _passphrase())
        if not rep["hypotheses"]:
            print("No hypotheses yet — add some, then add and rate evidence.")
            return 0
        print(f"Question: {rep['question'] or '—'}")
        for h in rep["hypotheses"]:
            print(f"  #{h['rank']} [{h['weighted_inconsistency']:>3} against] {h['label']}  "
                  f"(consistency {h['weighted_consistency']}, {h['rated_evidence']} rated)")
        if rep["leading"]:
            tail = (f"separation {rep['separation']}"
                    + ("" if rep["decisive"] else " — not yet decisive")) if rep["separation"] is not None else ""
            print(f"Least contradicted: {rep['leading']['label']}  {tail}")
        diag = [d for d in rep["evidence_diagnosticity"] if d["diagnostic"]]
        if diag:
            print(f"Most diagnostic: {diag[0]['text'][:70]} (diagnosticity {diag[0]['diagnosticity']})")
        return 0
    if args.command == "matrix":
        print(json.dumps(matrix(args.analysis, _passphrase()), indent=2, ensure_ascii=False))
        return 0
    if args.command == "export":
        result = export(args.analysis, args.destination, _passphrase(), args.format)
        print(f"export ({result['format']}): wrote {result['output']}  sha256 {result['sha256']}")
        return 0
    if args.command == "inspect":
        print(json.dumps(inspect(args.analysis, _passphrase()), indent=2, ensure_ascii=False))
        return 0
    return 0


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command in (None, "gui"):
        from .gui import run_app
        return run_app()
    try:
        return _run(args)
    except HypothesisError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        print("\nCancelled.", file=sys.stderr)
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
