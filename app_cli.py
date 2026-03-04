#!/usr/bin/env python3
from __future__ import annotations

import logging
import os
import sys
import traceback
from datetime import datetime
from pathlib import Path
from typing import Optional

try:
    import tkinter as tk
    from tkinter import filedialog
except Exception:
    tk = None
    filedialog = None

LOGGER: Optional[logging.Logger] = None


def read_with_default(prompt: str, default: str) -> str:
    value = input(f"{prompt} [{default}]: ").strip()
    return value or default


def get_base_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


def setup_logging(base_dir: Path) -> Path:
    global LOGGER

    logs_dir = base_dir / "logs"
    logs_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    log_path = logs_dir / f"domains_scorer_{timestamp}.log"

    LOGGER = logging.getLogger("domains_scorer")
    LOGGER.handlers.clear()
    LOGGER.setLevel(logging.INFO)
    LOGGER.propagate = False

    handler = logging.FileHandler(log_path, encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s | %(levelname)s | %(message)s"))
    LOGGER.addHandler(handler)
    LOGGER.info("Run started")

    return log_path


def log_info(message: str) -> None:
    print(message)
    if LOGGER:
        LOGGER.info(message)


def log_error(message: str) -> None:
    print(message, file=sys.stderr)
    if LOGGER:
        LOGGER.error(message)


def pause_if_interactive(message: str = "Press Enter to exit.") -> None:
    should_pause = sys.stdin.isatty() or getattr(sys, "frozen", False)
    if not should_pause:
        return

    if os.name == "nt":
        try:
            import msvcrt

            print(message, end="", flush=True)
            msvcrt.getch()
            print()
            return
        except Exception:
            pass

    try:
        input(message)
    except EOFError:
        pass


def browse_for_file(initial_dir: Path) -> Optional[str]:
    if tk is None or filedialog is None:
        return None

    try:
        root = tk.Tk()
        root.withdraw()
        try:
            root.attributes("-topmost", True)
        except Exception:
            pass
        selected = filedialog.askopenfilename(
            title="Select domains file",
            initialdir=str(initial_dir),
            filetypes=[
                ("Text files", "*.txt"),
                ("CSV files", "*.csv"),
                ("All files", "*.*"),
            ],
        )
        root.destroy()
        return selected or None
    except Exception:
        return None


def prompt_for_existing_file(prompt: str, default: str, initial_dir: Optional[Path] = None) -> str:
    file_picker_failed = False
    while True:
        selected = browse_for_file(initial_dir or Path(default).parent)
        if selected:
            value = selected
        else:
            if not file_picker_failed:
                if tk is None or filedialog is None:
                    log_info("File picker unavailable. Falling back to typed path input.")
                else:
                    log_info("File picker not used. Falling back to typed path input.")
                file_picker_failed = True
            value = read_with_default(prompt, default)
        path = Path(value)
        if not path.exists():
            log_error(f"Error: file not found: {path}")
            continue
        if not path.is_file():
            log_error(f"Error: not a file: {path}")
            continue
        return str(path)


def prompt_for_output_file(prompt: str, default: str) -> str:
    while True:
        value = read_with_default(prompt, default)
        path = Path(value)
        parent = path.parent
        if parent != Path(".") and not parent.exists():
            log_error(f"Error: output folder does not exist: {parent}")
            continue
        return str(path)


def prompt_for_positive_int(prompt: str, default: str) -> str:
    while True:
        value = read_with_default(prompt, default)
        try:
            parsed = int(value)
        except ValueError:
            log_error(f"Error: enter a whole number for {prompt.lower()}.")
            continue
        if parsed <= 0:
            log_error(f"Error: {prompt} must be greater than 0.")
            continue
        return str(parsed)


def prompt_for_positive_float(prompt: str, default: str) -> str:
    while True:
        value = read_with_default(prompt, default)
        try:
            parsed = float(value)
        except ValueError:
            log_error(f"Error: enter a number for {prompt.lower()}.")
            continue
        if parsed <= 0:
            log_error(f"Error: {prompt} must be greater than 0.")
            continue
        return str(parsed)


def prompt_for_yes_no(prompt: str, default: str) -> str:
    while True:
        value = read_with_default(prompt, default)
        if value.lower() in {"y", "yes", "n", "no"}:
            return value
        log_error("Error: enter Y or N.")


def main() -> int:
    try:
        base_dir = get_base_dir()
        os.chdir(base_dir)
        log_path = setup_logging(base_dir)

        import extract_features
        import finalize_scores

        log_info("Welcome to the domains scorer.")
        log_info("Please wait while the file picker opens, then choose your domains list.")
        log_info(f"Log file: {log_path}")

        input_file = prompt_for_existing_file(
            "Input domains file",
            str(base_dir / "input" / "domains.txt"),
            initial_dir=base_dir / "input",
        )
        features_file = prompt_for_output_file("Features output JSONL", str(base_dir / "features.jsonl"))
        concurrency = prompt_for_positive_int("Concurrency", "60")
        pages = prompt_for_positive_int("Pages per domain", "6")
        timeout = prompt_for_positive_float("Timeout (seconds)", "10")
        resume_answer = prompt_for_yes_no("Resume from existing features file? (Y/N)", "Y")
        if LOGGER:
            LOGGER.info(
                "Inputs accepted | input_file=%s | features_file=%s | concurrency=%s | pages=%s | timeout=%s | resume=%s",
                input_file,
                features_file,
                concurrency,
                pages,
                timeout,
                resume_answer,
            )

        extract_argv = [
            "--input", input_file,
            "--out-jsonl", features_file,
            "--concurrency", concurrency,
            "--pages", pages,
            "--timeout", timeout,
        ]
        if resume_answer.lower() in {"y", "yes"}:
            extract_argv.append("--resume")

        log_info("Starting feature extraction.")
        rc = extract_features.main(extract_argv)
        if rc != 0:
            log_error(f"Feature extraction failed with exit code {rc}. Scoring step was skipped.")
            return rc

        score_argv = [
            "--features-jsonl", features_file,
        ]
        log_info("Starting scoring.")
        rc = finalize_scores.main(score_argv)
        if rc != 0:
            log_error(f"Scoring failed with exit code {rc}.")
            return rc

        output_dir = Path("output")
        if output_dir.exists():
            log_info(f"Scored files are in: {output_dir.resolve()}")

        log_info("Run completed successfully.")

        return 0
    except KeyboardInterrupt:
        log_error("Cancelled by user.")
        return 130
    except Exception:
        log_error("Unhandled error. See traceback below.")
        traceback_text = traceback.format_exc()
        print(traceback_text, file=sys.stderr, end="")
        if LOGGER:
            LOGGER.error("Unhandled exception:\n%s", traceback_text.rstrip())
        return 1


if __name__ == "__main__":
    rc = main()
    if rc != 0:
        pause_if_interactive("Application failed. Press Enter to exit.")
    raise SystemExit(rc)
