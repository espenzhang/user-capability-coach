#!/usr/bin/env python3
"""Fixture-driven offline evaluation for Milestone 0 targets.

Runs the detector + policy pipeline against all fixture JSONL files and
reports accuracy metrics. Target thresholds:
  - Good prompt false positive rate: < 5%
  - v1 issue detection rate on weak prompts: > 80%
  - Sensitive prompts suppression rate: 100%

Usage:
    python3 tests/eval_fixtures.py
    python3 tests/eval_fixtures.py --verbose
"""
from __future__ import annotations
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from tools.detectors import detect
from tools.policy import select_action, PolicyInput
from tools.taxonomy import CoachMode, Action


def _make_policy_input(text: str, detection=None):
    from tools.detectors import detect as _detect
    if detection is None:
        detection = _detect(text)
    return PolicyInput(
        mode=CoachMode.LIGHT,
        detection=detection,
        memory_enabled=True,
        observation_period_ends_at=None,
        proactive_count_7d=0,
        retrospective_count_7d=0,
        last_notified_pattern=None,
        last_notified_at=None,
        user_dismissed_recently=False,
        top_pattern=None,
    )


def eval_good_prompts(fixtures_dir: Path, verbose: bool = False) -> dict:
    path = fixtures_dir / "good_prompts.jsonl"
    total = 0
    false_positives = 0
    fp_examples = []

    with open(path) as f:
        for line in f:
            item = json.loads(line)
            text = item["text"]
            detection = detect(text)
            inp = _make_policy_input(text, detection)
            result = select_action(inp)

            total += 1
            is_coaching = result.action in (
                Action.POST_ANSWER_TIP,
                Action.PRE_ANSWER_MICRO_NUDGE,
            )
            if is_coaching:
                false_positives += 1
                fp_examples.append({
                    "id": item.get("id"),
                    "text": text[:80],
                    "action": result.action.value,
                    "issue": result.issue_type.value if result.issue_type else None,
                })

    rate = false_positives / total if total else 0
    return {
        "total": total,
        "false_positives": false_positives,
        "fp_rate": rate,
        "target": 0.05,
        "passed": rate <= 0.05,
        "examples": fp_examples if verbose else [],
    }


def eval_weak_prompts(fixtures_dir: Path, verbose: bool = False) -> dict:
    path = fixtures_dir / "weak_prompts.jsonl"
    total = 0
    detected = 0
    issue_hits = 0
    misses = []

    with open(path) as f:
        for line in f:
            item = json.loads(line)
            text = item["text"]
            expected_issue = item.get("expected_issue")
            detection = detect(text)
            inp = _make_policy_input(text, detection)
            result = select_action(inp)

            total += 1
            is_coaching = result.action in (
                Action.POST_ANSWER_TIP,
                Action.PRE_ANSWER_MICRO_NUDGE,
                Action.SILENT_REWRITE,
            )
            if is_coaching or result.action != Action.NONE:
                detected += 1

            actual_issue = result.issue_type.value if result.issue_type else None
            if actual_issue == expected_issue:
                issue_hits += 1
            elif verbose:
                misses.append({
                    "id": item.get("id"),
                    "text": text[:80],
                    "expected": expected_issue,
                    "got": actual_issue,
                    "action": result.action.value,
                })

    rate = issue_hits / total if total else 0
    return {
        "total": total,
        "issue_hits": issue_hits,
        "hit_rate": rate,
        "target": 0.80,
        "passed": rate >= 0.80,
        "misses": misses if verbose else [],
    }


def eval_sensitive_prompts(fixtures_dir: Path, verbose: bool = False) -> dict:
    path = fixtures_dir / "sensitive_prompts.jsonl"
    total = 0
    correctly_suppressed = 0
    leaks = []

    with open(path) as f:
        for line in f:
            item = json.loads(line)
            text = item["text"]
            detection = detect(text)
            inp = _make_policy_input(text, detection)
            result = select_action(inp)

            total += 1
            is_suppressed = result.action in (Action.NONE, Action.SILENT_REWRITE)
            if is_suppressed:
                correctly_suppressed += 1
            else:
                leaks.append({
                    "id": item.get("id"),
                    "text": text[:80],
                    "action": result.action.value,
                })

    rate = correctly_suppressed / total if total else 0
    return {
        "total": total,
        "suppressed": correctly_suppressed,
        "suppression_rate": rate,
        "target": 1.0,
        "passed": rate >= 1.0,
        "leaks": leaks if verbose else [],
    }


def main():
    parser = argparse.ArgumentParser(description="Fixture-based offline eval")
    parser.add_argument("--verbose", "-v", action="store_true")
    parser.add_argument(
        "--fixtures",
        default=str(Path(__file__).parent / "fixtures"),
    )
    args = parser.parse_args()

    fixtures_dir = Path(args.fixtures)
    verbose = args.verbose

    print("=" * 60)
    print("User Capability Coach — Offline Fixture Evaluation")
    print("=" * 60)

    good = eval_good_prompts(fixtures_dir, verbose)
    weak = eval_weak_prompts(fixtures_dir, verbose)
    sensitive = eval_sensitive_prompts(fixtures_dir, verbose)

    # Report
    status_good = "✅ PASS" if good["passed"] else "❌ FAIL"
    status_weak = "✅ PASS" if weak["passed"] else "❌ FAIL"
    status_sens = "✅ PASS" if sensitive["passed"] else "❌ FAIL"

    print(f"\n[1] Good prompts — False positive rate")
    print(f"    {status_good}  {good['false_positives']}/{good['total']} false positives "
          f"({good['fp_rate']:.1%} vs target <{good['target']:.0%})")
    if verbose and good["examples"]:
        print("    False positive examples:")
        for ex in good["examples"]:
            print(f"      [{ex['id']}] action={ex['action']} issue={ex['issue']}")
            print(f"        text: {ex['text']}")

    print(f"\n[2] Weak prompts — Issue detection rate")
    print(f"    {status_weak}  {weak['issue_hits']}/{weak['total']} correct issue type "
          f"({weak['hit_rate']:.1%} vs target ≥{weak['target']:.0%})")
    if verbose and weak["misses"]:
        print("    Misses:")
        for m in weak["misses"][:10]:
            print(f"      [{m['id']}] expected={m['expected']} got={m['got']} action={m['action']}")
            print(f"        text: {m['text']}")

    print(f"\n[3] Sensitive prompts — Suppression rate")
    print(f"    {status_sens}  {sensitive['suppressed']}/{sensitive['total']} suppressed "
          f"({sensitive['suppression_rate']:.1%} vs target ≥{sensitive['target']:.0%})")
    if verbose and sensitive["leaks"]:
        print("    Leaks (coaching shown in sensitive context):")
        for leak in sensitive["leaks"]:
            print(f"      [{leak['id']}] action={leak['action']}")
            print(f"        text: {leak['text']}")

    all_passed = good["passed"] and weak["passed"] and sensitive["passed"]
    print(f"\n{'=' * 60}")
    if all_passed:
        print("✅ All milestone-zero targets passed. Ready for M1.")
    else:
        print("❌ Some targets not met. Review before proceeding.")
    print("=" * 60)

    sys.exit(0 if all_passed else 1)


if __name__ == "__main__":
    main()
