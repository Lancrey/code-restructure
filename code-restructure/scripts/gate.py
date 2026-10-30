#!/usr/bin/env python3
"""One command for the verification gates, so no step can be forgotten.

  gate.py <repo> --plan <plan.md> --report-dir <dir> --record-before [--only ids] [--extra cmd]...
      Before any move: baseline-before.json, references-before.json and secrets-redacted.txt in
      <report-dir>/restructure-evidence/.

  gate.py <repo> --plan <plan.md> --report-dir <dir> --step N [--only ids] [--extra cmd]... [--allow glob]...
      After migration step N: stale references and relocation table (rows not reached yet are
      pending), baseline compared with "before", secrets verify on the reports and evidence.

  gate.py <repo> --plan <plan.md> --report-dir <dir> --final [...]
      Phase 4: every row of the plan must be done; secrets verify in --strict mode.

Each gate prints PASS / FAIL / UNVERIFIED with the underlying command's output, and the exit code is
1 if any gate failed, 0 otherwise. It only runs the other scripts of this folder; it never moves,
edits or commits anything.
"""
import argparse
import glob
import json
import shlex
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
LAST_OUTPUT = [""]


def run(label, cmd, ok_codes=(0,), unverified_codes=(), review_codes=()):
    print(f"\n=== {label}\n$ {shlex.join(str(c) for c in cmd)}", flush=True)
    r = subprocess.run([sys.executable, *cmd], capture_output=True, text=True)
    out = (r.stdout + r.stderr).strip()
    LAST_OUTPUT[0] = out
    if out:
        print(out)
    if r.returncode in review_codes:
        verdict = "REVIEW"
    elif r.returncode in ok_codes:
        verdict = "PASS"
    elif r.returncode in unverified_codes:
        verdict = "UNVERIFIED"
    else:
        verdict = "FAIL"
    print(f"--- {label}: {verdict} (exit {r.returncode})")
    return verdict


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("repo")
    ap.add_argument("--plan", required=True, help="migration plan with the From|To table (path relative to the repo or absolute)")
    ap.add_argument("--report-dir", required=True, help="where the three architecture-*.md reports live (relative to the repo or absolute)")
    mode = ap.add_mutually_exclusive_group(required=True)
    mode.add_argument("--record-before", action="store_true")
    mode.add_argument("--step", type=int, metavar="N")
    mode.add_argument("--final", action="store_true")
    ap.add_argument("--only", help="baseline.py --only, must be the same in every call")
    ap.add_argument("--extra", action="append", default=[], help="baseline.py --extra, repeatable, must be the same in every call")
    ap.add_argument("--compare-output", action="append", default=[], metavar="DIR",
                    help="baseline.py --compare-output: build output folder compared file by file (dist/ of a published package)")
    ap.add_argument("--allow", action="append", default=[], help="check_moves.py --allow, repeatable (compatibility shims)")
    ap.add_argument("--ref", help="record the 'before' baseline from this git ref (worktree) instead of the working tree")
    args = ap.parse_args()

    repo = Path(args.repo).resolve()
    report_dir = Path(args.report_dir) if Path(args.report_dir).is_absolute() else repo / args.report_dir
    plan = Path(args.plan) if Path(args.plan).is_absolute() else repo / args.plan
    ev = report_dir / "restructure-evidence"
    ev.mkdir(parents=True, exist_ok=True)
    base_args = (["--only", args.only] if args.only else []) + [a for x in args.extra for a in ("--extra", x)] \
        + [a for x in args.compare_output for a in ("--compare-output", x)]
    allow_args = [a for x in args.allow for a in ("--allow", x)]
    verdicts = {}

    if args.record_before:
        if not plan.exists():
            print(f"plan not found: {plan} — write the migration plan first, the gates read its relocation table", file=sys.stderr)
            sys.exit(2)
        verdicts["baseline before"] = run("baseline before", [HERE / "baseline.py", repo, "--out", ev / "baseline-before.json"]
                                          + (["--ref", args.ref] if args.ref else []) + base_args)
        try:
            checks = json.loads((ev / "baseline-before.json").read_text())["checks"]
            green = [c["id"] for c in checks if c["status"] == "pass"]
            counted = [c["id"] for c in checks if c["status"] == "fail" and c.get("counters")]
            if not green and not counted:
                print("\nWARNING: no check passes on the untouched tree and none reports counts: the comparison can only say "
                      "'still failing'. Build a verification floor that passes (or prints 'N passed, M failed') and pass it with --extra.")
                verdicts["baseline before"] = "UNVERIFIED"
            elif not green:
                print(f"\nnote: no check passes before the migration; regressions will be judged on the counters of {', '.join(counted)} only.")
            blind = [c["id"] for c in checks if c["status"] != "pass" and not c.get("counters")]
            if blind:
                print(f"\nnote: {', '.join(blind)} already fail(s) and print(s) no counts: before/after can only say 'still failing', "
                      "so they protect nothing. Rely on the other checks, or replace them with a floor that prints 'N passed, M failed'.")
        except (OSError, ValueError, KeyError):
            pass
        verdicts["references before"] = run("references before (what is already unresolved)",
                                            [HERE / "check_moves.py", repo, "--plan", plan, "--in-progress", "--json", ev / "references-before.json"] + allow_args,
                                            ok_codes=(0, 1))  # hits are expected here: nothing has moved yet
        verdicts["secrets scan"] = run("secrets scan (redacted list)", [HERE / "secrets_scan.py", repo, "--plan", plan, "--out", ev / "secrets-redacted.txt"])
    else:
        tag = "final" if args.final else f"step-{args.step}"
        before_refs = ev / "references-before.json"
        before_base = ev / "baseline-before.json"
        missing = [p for p in (before_refs, before_base) if not p.exists()]
        if missing:
            print("missing: " + ", ".join(str(m) for m in missing) + " — run gate.py --record-before on the untouched tree first", file=sys.stderr)
            sys.exit(2)
        cm = [HERE / "check_moves.py", repo, "--plan", plan, "--before", before_refs, "--json", ev / f"references-{tag}.json"] + allow_args
        if not args.final:
            cm.append("--in-progress")
        verdicts["moves"] = run(f"relocation table, stale references, unresolved references ({tag})", cm)
        verdicts["baseline"] = run(f"baseline compared with before ({tag})",
                                   [HERE / "baseline.py", repo, "--out", ev / f"baseline-{tag}.json", "--compare", before_base] + base_args)
        reports = sorted(glob.glob(str(report_dir / "architecture-*.md")))
        sv = [HERE / "secrets_scan.py", repo, "--verify", *reports, ev]
        verdicts["secrets"] = run(f"no secret value in reports or evidence ({tag})", sv)
        if args.final:
            # strict mode also checks plain-word values ("playlist", "layout"): it cannot be green on a real repo,
            # so it is a list to REVIEW by hand, recorded in the validation report — not a gate
            # exit 1 here means "there is a list to read", not a failed gate
            verdicts["secrets strict (review)"] = run("strict secrets check, plain-word values included (review by hand)",
                                                      sv + ["--strict"], review_codes=(1,))
        if any(repo.glob("*.xcodeproj")):
            verdicts["xcode"] = run("Xcode project file vs disk", [HERE / "pbxproj_check.py", repo], unverified_codes=(3,))

    print("\n=== Summary")
    for k, v in verdicts.items():
        print(f"  {v:<10} {k}")
    if "FAIL" in verdicts.values():
        print("\nAt least one gate failed: fix or revert this step before going on.")
        sys.exit(1)
    if "UNVERIFIED" in verdicts.values():
        print("\nSome gate could not verify anything here: list it under 'Not verified' in the validation report.")
    if args.record_before:
        print("\nBefore-state recorded (failing checks are recorded as they are; they are compared, not judged, from now on).")
        return
    if "REVIEW" in verdicts.values():
        print("\nGates passed; one list still needs a human review: check each strict-secrets entry (name and source line are\n"
              "printed) and record the outcome in the validation report — a real secret there must be removed before committing.")
        return
    print("\nAll gates passed (evidence, not proof).")


if __name__ == "__main__":
    main()
