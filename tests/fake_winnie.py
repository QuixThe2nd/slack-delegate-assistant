#!/usr/bin/env python3
"""Test double for the Winnie runner command."""

import os
import sys


def main() -> None:
    prompt = sys.stdin.read()
    log_path = os.environ.get("WINNIE_TEST_PROMPT_LOG")
    if log_path:
        with open(log_path, "w", encoding="utf-8") as log:
            log.write(prompt)
    sys.stdout.write("Winnie completed: " + prompt)


if __name__ == "__main__":
    main()
