"""Read-only, privacy-preserving diagnostics for the Kingshot resource dialog.

A working Android network/Play Store does not prove that Kingshot's resource CDN
is reachable. These probes never change DNS, VPN, app data or network settings,
never contact undocumented game endpoints and never persist raw logcat output.
"""
from __future__ import annotations

import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


NETWORK_ERROR_PATTERNS = {
    "dns_lookup": r"UnknownHostException|EAI_AGAIN|NXDOMAIN|no address associated",
    "tls_handshake": r"SSLHandshakeException|CERTIFICATE_VERIFY_FAILED|CertPathValidatorException",
    "connection_failure": r"ConnectException|ECONNREFUSED|ENETUNREACH|EHOSTUNREACH",
    "connection_timeout": r"SocketTimeoutException|ETIMEDOUT|Read timed out",
    "http_auth_or_forbidden": r"\bHTTP[/ ]?(?:401|403)\b|\bresponseCode[=: ]+(?:401|403)\b",
    "http_rate_limit": r"\bHTTP[/ ]?429\b|\bresponseCode[=: ]+429\b",
    "http_server_failure": r"\bHTTP[/ ]?5\d\d\b|\bresponseCode[=: ]+5\d\d\b",
    "resource_download": r"UnityWebRequest|AssetBundle|Addressables|DownloadHandler",
}


def _query(backend: Any, argv: list[str], *, timeout: int = 7) -> tuple[str, str]:
    try:
        return str(backend.shell(argv, timeout=timeout) or "").strip(), "ok"
    except Exception as exc:
        # Persist error type only: arbitrary external stderr might contain URLs,
        # bearer tokens or account data.
        return "", type(exc).__name__


def collect_resource_network_diagnostics(
    backend: Any, *, run_id: str = "", head: str = "",
    phase: str = "", step: str = "", attempts: int = 0,
) -> dict[str, Any]:
    """Collect bounded read-only signals; results are clues, not CDN proof."""
    report: dict[str, Any] = {
        "schema": 1,
        "kind": "kingshot-resource-loading",
        "captured_at_utc": datetime.now(timezone.utc).isoformat(),
        "run_id": str(run_id),
        "head": str(head),
        "phase": str(phase),
        "step": str(step),
        "confirmed_retries": max(0, int(attempts)),
        "status": "external_resource_loading_unresolved",
        "non_destructive": True,
        "game_cdn_reachable": None,
        "note": (
            "Android connectivity and Play Store acceptance cannot establish "
            "whether Kingshot asset/CDN endpoints are reachable; no endpoint "
            "is guessed and no settings are changed."
        ),
        "signals": {},
        "probe_errors": {},
    }
    signals = report["signals"]
    errors = report["probe_errors"]

    def sample(key: str, argv: list[str], *, timeout: int = 7) -> str:
        value, status = _query(backend, argv, timeout=timeout)
        if status != "ok":
            errors[key] = status
        return value

    mode = sample("private_dns_mode", ["settings", "get", "global", "private_dns_mode"])
    signals["private_dns_mode"] = mode if mode in ("off", "opportunistic", "hostname") else "unknown"
    hostname = sample("private_dns_specifier", ["settings", "get", "global", "private_dns_specifier"])
    signals["private_dns_hostname_configured"] = bool(hostname and hostname.lower() not in ("null", "none"))
    proxy = sample("http_proxy", ["settings", "get", "global", "http_proxy"])
    signals["android_proxy_configured"] = bool(
        proxy and proxy.lower() not in ("null", "none", ":0", ""))
    connectivity = sample("connectivity", ["dumpsys", "connectivity"], timeout=9)
    # Dumpsys may include stale networks. The signals are explicitly non-
    # authoritative, and raw hostnames/IPs are intentionally discarded.
    signals["validated_capability_mentioned"] = bool(re.search(r"\bVALIDATED\b", connectivity))
    signals["vpn_transport_mentioned"] = bool(re.search(r"TRANSPORT_VPN|\bVPN\b", connectivity))
    pid_text = sample("game_pid", ["pidof", getattr(backend, "package", "com.got.globalru")])
    pid = next((word for word in pid_text.split() if word.isdigit()), "")
    signals["game_process_present"] = bool(pid)
    if pid:
        logs = sample("game_logcat", ["logcat", "-d", "--pid=" + pid, "-t", "600"], timeout=10)
        signals["game_logcat_error_counts"] = {
            label: min(999, len(re.findall(pattern, logs, flags=re.IGNORECASE)))
            for label, pattern in NETWORK_ERROR_PATTERNS.items()
        }
    else:
        signals["game_logcat_error_counts"] = None
    return report


def save_resource_network_diagnostics(path: str | Path, report: dict[str, Any]) -> None:
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_name(destination.name + ".tmp")
    with temporary.open("w", encoding="utf-8") as stream:
        json.dump(report, stream, ensure_ascii=False, indent=2)
    os.replace(temporary, destination)
