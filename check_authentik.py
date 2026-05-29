#!/usr/bin/env python3
#########################################################################
# Written by Fabian Ihle, fabi@ihlecloud.de                             #
# Created: 29.05.2026                                                   #
# github: https://github.com/n1tr0-5urf3r/check_authentik               #
#                                                                       #
# Monitors Authentik health for Icinga / Nagios                         #
# Checks system, version, tasks, workers, and outposts                  #
# Provides visible metrics and Nagios performance data                  #
# --------------------------------------------------------------------- #
# Changelog:                                                            #
# 290526 Version 1.00 - Initial Authentik monitoring plugin             #
#########################################################################
"""Nagios/Icinga check plugin for Authentik."""

import argparse
import getpass
import os
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urlencode

import requests


OK = 0
WARNING = 1
CRITICAL = 2
UNKNOWN = 3

STATE_NAMES = {
    OK: "OK",
    WARNING: "WARNING",
    CRITICAL: "CRITICAL",
    UNKNOWN: "UNKNOWN",
}

TASK_STATES = (
    "queued",
    "consumed",
    "preprocess",
    "running",
    "postprocess",
    "rejected",
    "done",
    "info",
    "warning",
    "error",
)


class CheckError(Exception):
    def __init__(self, state: int, message: str) -> None:
        super().__init__(message)
        self.state = state
        self.message = message


@dataclass
class CheckResult:
    state: int
    message: str
    perfdata: dict[str, int | float] = field(default_factory=dict)


class AuthentikClient:
    def __init__(self, base_url: str, token: str, timeout: int, verify_tls: bool) -> None:
        self.base_url = normalize_api_url(base_url)
        self.timeout = timeout
        self.verify_tls = verify_tls
        self.session = requests.Session()
        self.session.headers.update(
            {
                "Accept": "application/json",
                "Authorization": f"Bearer {token}",
                "User-Agent": "check-authentik/1.0",
            }
        )

    def get(self, path: str) -> Any:
        url = f"{self.base_url}{path}"
        try:
            response = self.session.get(
                url,
                timeout=self.timeout,
                verify=self.verify_tls,
            )
        except requests.exceptions.SSLError as error:
            raise CheckError(CRITICAL, f"TLS error for {path}: {error}") from error
        except requests.exceptions.Timeout as error:
            raise CheckError(CRITICAL, f"timeout for {path}") from error
        except requests.exceptions.RequestException as error:
            raise CheckError(CRITICAL, f"request failed for {path}: {error}") from error

        if response.status_code in (401, 403):
            raise CheckError(UNKNOWN, f"auth/permission error for {path}: HTTP {response.status_code}")
        if response.status_code >= 500:
            raise CheckError(CRITICAL, f"server error for {path}: HTTP {response.status_code}")
        if response.status_code >= 400:
            raise CheckError(UNKNOWN, f"unexpected response for {path}: HTTP {response.status_code}")

        try:
            return response.json()
        except ValueError as error:
            raise CheckError(UNKNOWN, f"invalid JSON from {path}") from error


def normalize_api_url(url: str) -> str:
    normalized = url.rstrip("/")
    if normalized.endswith("/api/v3"):
        return normalized
    return f"{normalized}/api/v3"


def parse_time(value: str, field_name: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as error:
        raise CheckError(UNKNOWN, f"invalid timestamp in {field_name}: {value}") from error
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def check_system(client: Any, args: argparse.Namespace) -> CheckResult:
    data = client.get("/admin/system/")
    if not isinstance(data, dict) or not isinstance(data.get("runtime"), dict):
        raise CheckError(UNKNOWN, "unexpected system response shape")

    runtime = data["runtime"]
    version = runtime.get("authentik_version")
    if not version:
        raise CheckError(UNKNOWN, "system response has no authentik runtime version")

    server_time = data.get("server_time")
    if not server_time:
        raise CheckError(UNKNOWN, "system response has no server_time")

    skew = abs((datetime.now(timezone.utc) - parse_time(server_time, "server_time")).total_seconds())
    perfdata = {"clock_skew": round(skew, 3)}
    if skew > args.max_clock_skew:
        return CheckResult(
            WARNING,
            f"system clock skew {int(skew)}s exceeds {args.max_clock_skew}s",
            perfdata,
        )
    return CheckResult(OK, f"system ok version={version}", perfdata)


def check_version(client: Any, _args: argparse.Namespace) -> CheckResult:
    data = client.get("/admin/version/")
    if not isinstance(data, dict):
        raise CheckError(UNKNOWN, "unexpected version response shape")

    current = data.get("version_current", "unknown")
    latest = data.get("version_latest", "unknown")
    problems: list[str] = []

    if data.get("outdated"):
        problems.append(f"authentik update available {current}->{latest}")
    if data.get("outpost_outdated"):
        problems.append("one or more outposts are outdated")
    if data.get("version_latest_valid") is False:
        problems.append("latest version cache is invalid")

    perfdata = {
        "authentik_outdated": int(bool(data.get("outdated"))),
        "outpost_outdated": int(bool(data.get("outpost_outdated"))),
    }
    if problems:
        return CheckResult(WARNING, "; ".join(problems), perfdata)
    return CheckResult(OK, f"version ok current={current} latest={latest}", perfdata)


def recent_tasks(client: Any, status: str, max_age: int) -> list[dict[str, Any]]:
    query = urlencode({"aggregated_status": status, "ordering": "-mtime", "page_size": 50})
    data = client.get(f"/tasks/tasks/?{query}")
    if not isinstance(data, dict) or not isinstance(data.get("results"), list):
        raise CheckError(UNKNOWN, f"unexpected {status} task list response shape")

    now = datetime.now(timezone.utc)
    tasks = []
    for task in data["results"]:
        if not isinstance(task, dict):
            raise CheckError(UNKNOWN, f"unexpected {status} task item response shape")
        mtime = task.get("mtime")
        if mtime and (now - parse_time(mtime, "task mtime")).total_seconds() <= max_age:
            tasks.append(task)
    return tasks


def task_names(tasks: list[dict[str, Any]]) -> str:
    names = [str(task.get("actor_name") or task.get("message_id") or "unknown") for task in tasks[:3]]
    suffix = f", +{len(tasks) - 3} more" if len(tasks) > 3 else ""
    return ", ".join(names) + suffix


def check_tasks(client: Any, args: argparse.Namespace) -> CheckResult:
    data = client.get("/tasks/tasks/status/")
    if not isinstance(data, dict):
        raise CheckError(UNKNOWN, "unexpected task status response shape")

    counts = {name: int(data.get(name, 0) or 0) for name in TASK_STATES}
    perfdata = {f"tasks_{key}": value for key, value in counts.items()}
    recent_errors = recent_tasks(client, "error", args.task_max_age)
    recent_rejected = recent_tasks(client, "rejected", args.task_max_age)
    recent_warnings = recent_tasks(client, "warning", args.task_max_age)
    perfdata.update(
        {
            "tasks_recent_error": len(recent_errors),
            "tasks_recent_rejected": len(recent_rejected),
            "tasks_recent_warning": len(recent_warnings),
        }
    )

    if recent_errors:
        return CheckResult(
            CRITICAL,
            f"recent task errors within {args.task_max_age}s: {task_names(recent_errors)}",
            perfdata,
        )
    if recent_rejected:
        return CheckResult(
            WARNING,
            f"recent rejected tasks within {args.task_max_age}s: {task_names(recent_rejected)}",
            perfdata,
        )
    if recent_warnings:
        return CheckResult(
            WARNING,
            f"recent task warnings within {args.task_max_age}s: {task_names(recent_warnings)}",
            perfdata,
        )
    return CheckResult(OK, "tasks ok", perfdata)


def check_workers(client: Any, args: argparse.Namespace) -> CheckResult:
    workers = client.get("/tasks/workers/")
    if not isinstance(workers, list):
        raise CheckError(UNKNOWN, "unexpected workers response shape")

    mismatched = [
        str(worker.get("worker_id", "unknown"))
        for worker in workers
        if isinstance(worker, dict) and worker.get("version_matching") is False
    ]
    perfdata = {
        "workers": len(workers),
        "worker_version_mismatch": len(mismatched),
    }

    if len(workers) < args.min_workers:
        return CheckResult(CRITICAL, f"workers {len(workers)} below minimum {args.min_workers}", perfdata)
    if mismatched:
        return CheckResult(WARNING, f"worker version mismatch: {', '.join(mismatched)}", perfdata)
    return CheckResult(OK, f"workers ok count={len(workers)}", perfdata)


def check_outposts(client: Any, args: argparse.Namespace) -> CheckResult:
    data = client.get("/outposts/instances/")
    if not isinstance(data, dict) or not isinstance(data.get("results"), list):
        raise CheckError(UNKNOWN, "unexpected outposts response shape")

    outposts = data["results"]
    now = datetime.now(timezone.utc)
    stale_count = 0
    outdated_count = 0
    critical_messages: list[str] = []
    warning_messages: list[str] = []

    for outpost in outposts:
        if not isinstance(outpost, dict):
            raise CheckError(UNKNOWN, "unexpected outpost item response shape")
        outpost_id = outpost.get("pk")
        name = str(outpost.get("name") or outpost_id or "unknown")
        if not outpost_id:
            raise CheckError(UNKNOWN, f"outpost {name} has no pk")

        health_entries = client.get(f"/outposts/instances/{outpost_id}/health/")
        if not isinstance(health_entries, list):
            raise CheckError(UNKNOWN, f"unexpected health response for {name}")

        if not health_entries:
            stale_count += 1
            critical_messages.append(f"{name} has no health data")
            continue

        for entry in health_entries:
            if not isinstance(entry, dict):
                raise CheckError(UNKNOWN, f"unexpected health item for {name}")
            if entry.get("version_outdated"):
                outdated_count += 1
                warning_messages.append(f"{name} health {entry.get('uid', 'unknown')} version outdated")

            last_seen = entry.get("last_seen")
            if not last_seen:
                stale_count += 1
                critical_messages.append(f"{name} health {entry.get('uid', 'unknown')} has no last_seen")
                continue
            age = (now - parse_time(last_seen, "outpost last_seen")).total_seconds()
            if age > args.outpost_stale_seconds:
                stale_count += 1
                critical_messages.append(
                    f"{name} health {entry.get('uid', 'unknown')} stale {int(age)}s"
                )

    perfdata = {
        "outposts": len(outposts),
        "outpost_stale": stale_count,
        "outpost_version_outdated": outdated_count,
    }
    if critical_messages:
        return CheckResult(CRITICAL, "; ".join(critical_messages), perfdata)
    if warning_messages:
        return CheckResult(WARNING, "; ".join(warning_messages), perfdata)
    return CheckResult(OK, f"outposts ok count={len(outposts)}", perfdata)


CHECKS = {
    "system": check_system,
    "version": check_version,
    "tasks": check_tasks,
    "workers": check_workers,
    "outposts": check_outposts,
}


def load_token(args: argparse.Namespace) -> str:
    if args.token:
        return args.token.strip()
    if args.token_file:
        try:
            with open(args.token_file, "r", encoding="utf-8") as token_file:
                return token_file.read().strip()
        except OSError as error:
            raise CheckError(UNKNOWN, f"cannot read token file: {error}") from error
    token = os.environ.get("AUTHENTIK_TOKEN", "").strip()
    if token:
        return token
    raise CheckError(UNKNOWN, "missing token; use --token, --token-file, or AUTHENTIK_TOKEN")


def run_checks(client: Any, args: argparse.Namespace) -> CheckResult:
    check_names = list(CHECKS) if args.check == "all" else [args.check]
    results: list[CheckResult] = []
    for check_name in check_names:
        try:
            result = CHECKS[check_name](client, args)
        except CheckError as error:
            result = CheckResult(error.state, error.message)
        results.append(result)

    state = max(result.state for result in results)
    problems = [result.message for result in results if result.state != OK]
    messages = problems or [f"authentik checks passed: {', '.join(check_names)}"]
    perfdata: dict[str, int | float] = {}
    for result in results:
        perfdata.update(result.perfdata)
    return CheckResult(state, "; ".join(messages), perfdata)


def format_metrics(perfdata: dict[str, int | float]) -> tuple[str, str]:
    if not perfdata:
        return "", ""
    parts = [f"{key}={value}" for key, value in sorted(perfdata.items())]
    return "; metrics: " + ", ".join(parts), " | " + " ".join(parts)


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Nagios/Icinga check for Authentik.")
    parser.add_argument("--url", required=True, help="Authentik base URL, for example https://auth.example.com")
    parser.add_argument("--token", help="Authentik API bearer token")
    parser.add_argument("--token-file", help="File containing the Authentik API bearer token")
    parser.add_argument("--user", "--run-as", dest="run_as", help="Require the script to run as this local user")
    parser.add_argument("--insecure", action="store_true", help="Disable TLS certificate verification")
    parser.add_argument("--timeout", type=int, default=10, help="HTTP timeout in seconds")
    parser.add_argument(
        "--check",
        choices=["all", *CHECKS.keys()],
        default="all",
        help="Check to run. Default: all",
    )
    parser.add_argument("--min-workers", type=int, default=1, help="Minimum connected worker count")
    parser.add_argument("--max-clock-skew", type=int, default=300, help="Warning threshold for clock skew in seconds")
    parser.add_argument(
        "--task-max-age",
        type=int,
        default=3600,
        help="Only alert on error/rejected/warning tasks modified within this many seconds",
    )
    parser.add_argument(
        "--outpost-stale-seconds",
        type=int,
        default=300,
        help="Critical threshold for outpost health age in seconds",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(sys.argv[1:] if argv is None else argv)

    if args.run_as and getpass.getuser() != args.run_as:
        print(f"UNKNOWN - running as {getpass.getuser()}, expected {args.run_as}")
        return UNKNOWN

    if args.insecure:
        requests.packages.urllib3.disable_warnings()  # type: ignore[attr-defined]

    try:
        token = load_token(args)
        client = AuthentikClient(args.url, token, args.timeout, not args.insecure)
        result = run_checks(client, args)
    except CheckError as error:
        result = CheckResult(error.state, error.message)

    metric_text, perfdata = format_metrics(result.perfdata)
    print(f"{STATE_NAMES[result.state]} - {result.message}{metric_text}{perfdata}")
    return result.state


if __name__ == "__main__":
    sys.exit(main())
