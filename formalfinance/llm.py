from __future__ import annotations

from dataclasses import dataclass
from typing import Any
import json
import os
from urllib.request import Request, urlopen


def _parse_bool(value: Any, default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    lowered = str(value).strip().lower()
    if lowered in {"1", "true", "yes", "on"}:
        return True
    if lowered in {"0", "false", "no", "off"}:
        return False
    return default


def _int_or_default(value: Any, default: int) -> int:
    if value is None:
        return default
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


@dataclass(frozen=True)
class LLMConfig:
    enabled: bool = False
    provider: str = "none"
    model: str | None = None
    base_url: str | None = None
    api_key: str | None = None
    timeout_seconds: int = 20
    max_findings: int = 8

    @classmethod
    def disabled(cls) -> "LLMConfig":
        return cls()

    @classmethod
    def from_env(cls) -> "LLMConfig":
        return cls(
            enabled=_parse_bool(os.getenv("FORMALFINANCE_LLM_ENABLED"), default=False),
            provider=str(os.getenv("FORMALFINANCE_LLM_PROVIDER") or "none").strip().lower(),
            model=str(os.getenv("FORMALFINANCE_LLM_MODEL") or "").strip() or None,
            base_url=str(os.getenv("FORMALFINANCE_LLM_BASE_URL") or "").strip() or None,
            api_key=str(os.getenv("FORMALFINANCE_LLM_API_KEY") or "").strip() or None,
            timeout_seconds=max(3, _int_or_default(os.getenv("FORMALFINANCE_LLM_TIMEOUT"), 20)),
            max_findings=max(1, _int_or_default(os.getenv("FORMALFINANCE_LLM_MAX_FINDINGS"), 8)),
        )

    def with_overrides(self, payload: Any) -> "LLMConfig":
        if not isinstance(payload, dict):
            return self
        return LLMConfig(
            enabled=_parse_bool(payload.get("enabled"), default=self.enabled),
            provider=str(payload.get("provider") or self.provider or "none").strip().lower(),
            model=str(payload.get("model") or self.model or "").strip() or None,
            base_url=str(payload.get("base_url") or self.base_url or "").strip() or None,
            api_key=str(payload.get("api_key") or self.api_key or "").strip() or None,
            timeout_seconds=max(3, _int_or_default(payload.get("timeout_seconds"), self.timeout_seconds)),
            max_findings=max(1, _int_or_default(payload.get("max_findings"), self.max_findings)),
        )


def _http_json(
    url: str,
    payload: dict[str, Any],
    headers: dict[str, str],
    timeout_seconds: int,
) -> dict[str, Any]:
    body = json.dumps(payload).encode("utf-8")
    request = Request(url, data=body, method="POST", headers=headers)
    with urlopen(request, timeout=timeout_seconds) as response:
        raw = response.read().decode("utf-8")
    return json.loads(raw)


def _prompt_from_report(report: dict[str, Any], max_findings: int) -> str:
    findings = report.get("findings", []) or []
    lines: list[str] = []
    lines.append("You are an SEC filing compliance assistant.")
    lines.append("Provide remediation suggestions for failing checks only.")
    lines.append("Do not provide investment advice.")
    lines.append("Output strictly JSON with keys: summary, actions.")
    lines.append("actions is an array of {rule_id, priority, action}.")
    lines.append("")
    for finding in findings[:max_findings]:
        if not isinstance(finding, dict):
            continue
        severity = str(finding.get("severity") or "").strip().lower()
        if severity not in {"error", "warning"}:
            continue
        rule_id = str(finding.get("rule_id") or "")
        message = str(finding.get("message") or "")
        lines.append(f"- [{severity}] {rule_id}: {message}")
    return "\n".join(lines)


def _default_advisory(config: LLMConfig, status: str, **extra: Any) -> dict[str, Any]:
    payload = {
        "enabled": config.enabled,
        "provider": config.provider,
        "model": config.model,
        "status": status,
    }
    payload.update(extra)
    return payload


def _mock_suggestions(report: dict[str, Any], config: LLMConfig) -> dict[str, Any]:
    findings = report.get("findings", []) or []
    actions = []
    for finding in findings[: config.max_findings]:
        if not isinstance(finding, dict):
            continue
        severity = str(finding.get("severity") or "").lower()
        if severity not in {"error", "warning"}:
            continue
        actions.append(
            {
                "rule_id": finding.get("rule_id"),
                "priority": "high" if severity == "error" else "medium",
                "action": f"Review and remediate: {finding.get('message')}",
            }
        )
    return _default_advisory(
        config,
        "ok",
        summary="Mock LLM suggestions generated.",
        actions=actions,
    )


def _call_ollama(report: dict[str, Any], config: LLMConfig) -> dict[str, Any]:
    base = (config.base_url or "http://127.0.0.1:11434").rstrip("/")
    url = f"{base}/api/chat"
    model = config.model or "llama3.1:8b-instruct-q4_K_M"
    prompt = _prompt_from_report(report, config.max_findings)
    payload = {
        "model": model,
        "stream": False,
        "messages": [
            {"role": "system", "content": "You produce JSON only."},
            {"role": "user", "content": prompt},
        ],
    }
    result = _http_json(url, payload, {"Content-Type": "application/json"}, config.timeout_seconds)
    text = str((((result.get("message") or {}).get("content")) or "")).strip()
    if not text:
        return _default_advisory(config, "error", error="Empty response from Ollama.")
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        parsed = {"summary": "Non-JSON response from model.", "actions": [{"rule_id": None, "priority": "medium", "action": text}]}
    return _default_advisory(config, "ok", **parsed)


def _call_openai_compatible(report: dict[str, Any], config: LLMConfig) -> dict[str, Any]:
    base = (config.base_url or "https://api.openai.com/v1").rstrip("/")
    url = f"{base}/chat/completions"
    model = config.model or "gpt-4.1-mini"
    prompt = _prompt_from_report(report, config.max_findings)
    headers = {"Content-Type": "application/json"}
    if config.api_key:
        headers["Authorization"] = f"Bearer {config.api_key}"
    payload = {
        "model": model,
        "temperature": 0,
        "messages": [
            {"role": "system", "content": "You are a SEC filing compliance assistant. Return JSON only."},
            {"role": "user", "content": prompt},
        ],
    }
    result = _http_json(url, payload, headers, config.timeout_seconds)
    choices = result.get("choices") or []
    if not choices:
        return _default_advisory(config, "error", error="No choices returned by model endpoint.")
    content = str((((choices[0].get("message") or {}).get("content")) or "")).strip()
    if not content:
        return _default_advisory(config, "error", error="Empty completion content.")
    try:
        parsed = json.loads(content)
    except json.JSONDecodeError:
        parsed = {"summary": "Non-JSON response from model.", "actions": [{"rule_id": None, "priority": "medium", "action": content}]}
    return _default_advisory(config, "ok", **parsed)


def generate_advisory(report: dict[str, Any], config: LLMConfig) -> dict[str, Any]:
    if not config.enabled:
        return _default_advisory(config, "disabled")
    provider = (config.provider or "none").strip().lower()
    if provider in {"none", "off", "disabled"}:
        return _default_advisory(config, "disabled")
    if provider == "mock":
        return _mock_suggestions(report, config)
    try:
        if provider == "ollama":
            return _call_ollama(report, config)
        if provider in {"openai", "openai-compatible", "openai_compatible"}:
            return _call_openai_compatible(report, config)
        return _default_advisory(config, "error", error=f"Unsupported LLM provider '{provider}'.")
    except Exception as exc:
        return _default_advisory(config, "error", error=str(exc))
