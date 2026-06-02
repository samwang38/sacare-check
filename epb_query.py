#!/usr/bin/env python3
"""Run read-only SQL against EPBrowser WebService via the local Java helper."""
from __future__ import annotations

import argparse
import csv
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import List, Dict

DEFAULT_ROOT = Path(os.environ.get("EPB_LIVE_REPORT_ROOT", "/Users/sa/Claude/週報製作/報表製作_士林/live-report-app"))
DEFAULT_JAVA = os.environ.get("EPB_JAVA", "/Library/Java/JavaVirtualMachines/jdk1.8.0_251.jdk/Contents/Home/jre/bin/java")
DEFAULT_JAVAC = os.environ.get("EPB_JAVAC", "/Library/Java/JavaVirtualMachines/jdk1.8.0_251.jdk/Contents/Home/bin/javac")
DEFAULT_CP = os.environ.get("EPB_JAVA_CP", f"{DEFAULT_ROOT}:/Library/EPBrowser/EPB/Shell/lib/*:/Library/EPBrowser/EPB/Shell/shell.jar")


def compile_helper(root: Path, javac: str, java_cp: str) -> None:
    source = root / "EPBReportQuery.java"
    target = root / "EPBReportQuery.class"
    if not source.exists():
        raise FileNotFoundError(f"Missing helper source: {source}")
    if target.exists() and target.stat().st_mtime >= source.stat().st_mtime:
        return
    proc = subprocess.run([javac, "-cp", java_cp, str(source)], cwd=str(root), text=True, capture_output=True, timeout=30)
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr.strip() or proc.stdout.strip())


def run_sql(sql: str, limit: int, timeout: int, root: Path, java: str, javac: str, java_cp: str) -> str:
    compile_helper(root, javac, java_cp)
    proc = subprocess.run(
        [
            java,
            "-Dsun.net.client.defaultConnectTimeout=15000",
            "-Dsun.net.client.defaultReadTimeout=180000",
            "-cp",
            java_cp,
            "EPBReportQuery",
            sql,
            str(limit),
        ],
        cwd=str(root),
        text=True,
        capture_output=True,
        timeout=timeout,
    )
    if proc.returncode != 0:
        raise RuntimeError((proc.stderr or proc.stdout).strip())
    if proc.stderr.strip():
        print(proc.stderr.strip(), file=sys.stderr)
    return proc.stdout


def parse_tsv(text: str) -> List[Dict[str, str]]:
    lines = [line for line in text.splitlines() if line.strip()]
    if not lines:
        return []
    return list(csv.DictReader(lines, delimiter="\t"))


def main() -> int:
    parser = argparse.ArgumentParser(description="Run EPBrowser WebService SQL through EPBReportQuery.java")
    parser.add_argument("sql", nargs="?", help="SQL text. Use --sql-file for longer queries.")
    parser.add_argument("--sql-file", help="Path to a SQL file.")
    parser.add_argument("--format", choices=["tsv", "json", "csv"], default="tsv")
    parser.add_argument("--limit", type=int, default=100000)
    parser.add_argument("--timeout", type=int, default=240)
    parser.add_argument("--root", default=str(DEFAULT_ROOT))
    parser.add_argument("--java", default=DEFAULT_JAVA)
    parser.add_argument("--javac", default=DEFAULT_JAVAC)
    parser.add_argument("--classpath", default=DEFAULT_CP)
    args = parser.parse_args()

    if args.sql_file:
        sql = Path(args.sql_file).read_text(encoding="utf-8")
    elif args.sql:
        sql = args.sql
    else:
        sql = sys.stdin.read()
    sql = sql.strip()
    if not sql:
        parser.error("SQL is required via argument, --sql-file, or stdin")

    text = run_sql(sql, args.limit, args.timeout, Path(args.root), args.java, args.javac, args.classpath)
    if args.format == "tsv":
        print(text.rstrip())
    elif args.format == "json":
        print(json.dumps(parse_tsv(text), ensure_ascii=False, indent=2))
    else:
        rows = parse_tsv(text)
        if rows:
            writer = csv.DictWriter(sys.stdout, fieldnames=list(rows[0].keys()))
            writer.writeheader()
            writer.writerows(rows)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
