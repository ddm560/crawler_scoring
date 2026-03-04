#!/usr/bin/env python3
from __future__ import annotations

import os
import sys
import traceback
from pathlib import Path

import extract_features
import finalize_scores


def read_with_default(prompt: str, default: str) -> str:
    value = input(f"{prompt} [{default}]: ").strip()
    return value or default


def get_base_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


def pause_if_interactive(message: str = "Press Enter to exit.") -> None:
    if sys.stdin.isatty():
        try:
            input(message)
        except EOFError:
            pass


def prompt_for_existing_file(prompt: str, default: str) -> str:
    while True:
        value = read_with_default(prompt, default)
        path = Path(value)
        if not path.exists():
            print(f"Error: file not found: {path}", file=sys.stderr)
            continue
        if not path.is_file():
            print(f"Error: not a file: {path}", file=sys.stderr)
            continue
        return str(path)


def prompt_for_output_file(prompt: str, default: str) -> str:
    while True:
        value = read_with_default(prompt, default)
        path = Path(value)
        parent = path.parent
        if parent != Path(".") and not parent.exists():
            print(f"Error: output folder does not exist: {parent}", file=sys.stderr)
            continue
        return str(path)


def prompt_for_positive_int(prompt: str, default: str) -> str:
    while True:
        value = read_with_default(prompt, default)
        try:
            parsed = int(value)
        except ValueError:
            print(f"Error: enter a whole number for {prompt.lower()}.", file=sys.stderr)
            continue
        if parsed <= 0:
            print(f"Error: {prompt} must be greater than 0.", file=sys.stderr)
            continue
        return str(parsed)


def prompt_for_positive_float(prompt: str, default: str) -> str:
    while True:
        value = read_with_default(prompt, default)
        try:
            parsed = float(value)
        except ValueError:
            print(f"Error: enter a number for {prompt.lower()}.", file=sys.stderr)
            continue
        if parsed <= 0:
            print(f"Error: {prompt} must be greater than 0.", file=sys.stderr)
            continue
        return str(parsed)


def prompt_for_yes_no(prompt: str, default: str) -> str:
    while True:
        value = read_with_default(prompt, default)
        if value.lower() in {"y", "yes", "n", "no"}:
            return value
        print("Error: enter Y or N.", file=sys.stderr)


def main() -> int:
    try:
        base_dir = get_base_dir()
        os.chdir(base_dir)

        input_file = prompt_for_existing_file("Input domains file", str(base_dir / "input" / "domains.txt"))
        features_file = prompt_for_output_file("Features output JSONL", str(base_dir / "features.jsonl"))
        concurrency = prompt_for_positive_int("Concurrency", "60")
        pages = prompt_for_positive_int("Pages per domain", "6")
        timeout = prompt_for_positive_float("Timeout (seconds)", "10")
        resume_answer = prompt_for_yes_no("Resume from existing features file? (Y/N)", "Y")

        extract_argv = [
            "--input", input_file,
            "--out-jsonl", features_file,
            "--concurrency", concurrency,
            "--pages", pages,
            "--timeout", timeout,
        ]
        if resume_answer.lower() in {"y", "yes"}:
            extract_argv.append("--resume")

        rc = extract_features.main(extract_argv)
        if rc != 0:
            print("Feature extraction failed. Scoring step was skipped.", file=sys.stderr)
            return rc

        score_argv = [
            "--features-jsonl", features_file,
        ]
        rc = finalize_scores.main(score_argv)
        if rc != 0:
            print("Scoring failed.", file=sys.stderr)
            return rc

        output_dir = Path("output")
        if output_dir.exists():
            print(f"Scored files are in: {output_dir.resolve()}")

        return 0
    except KeyboardInterrupt:
        print("\nCancelled by user.", file=sys.stderr)
        return 130
    except Exception:
        print("Unhandled error:", file=sys.stderr)
        traceback.print_exc()
        return 1


if __name__ == "__main__":
    rc = main()
    if rc != 0:
        pause_if_interactive("Application failed. Press Enter to exit.")
    raise SystemExit(rc)
