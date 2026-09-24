#!/usr/bin/env python3
"""Run gh with a named stored account without changing global gh state."""

from __future__ import annotations

import argparse
import os
import subprocess
import sys


def run_capture(command: list[str], env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(command, env=env, text=True, capture_output=True, check=False)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--user", required=True, help="Stored GitHub login to use")
    parser.add_argument("--host", default="github.com")
    parser.add_argument("command", nargs=argparse.REMAINDER, help="Arguments passed to gh after --")
    args = parser.parse_args()

    command = args.command[1:] if args.command[:1] == ["--"] else args.command
    if not command:
        parser.error("provide a gh command after --")

    token_result = run_capture(
        ["gh", "auth", "token", "--hostname", args.host, "--user", args.user]
    )
    if token_result.returncode != 0:
        sys.stderr.write(token_result.stderr)
        return token_result.returncode

    token = token_result.stdout.strip()
    if not token:
        sys.stderr.write(f"gh returned no stored token for {args.user}@{args.host}\n")
        return 2

    env = os.environ.copy()
    env["GH_HOST"] = args.host
    env["GH_TOKEN"] = token

    identity = run_capture(["gh", "api", "user", "--jq", ".login"], env=env)
    if identity.returncode != 0:
        sys.stderr.write(identity.stderr)
        return identity.returncode
    actual = identity.stdout.strip()
    if actual.casefold() != args.user.casefold():
        sys.stderr.write(
            f"refusing command: requested GitHub actor {args.user!r}, token resolved to {actual!r}\n"
        )
        return 2

    completed = subprocess.run(["gh", *command], env=env, check=False)
    return completed.returncode


if __name__ == "__main__":
    raise SystemExit(main())
