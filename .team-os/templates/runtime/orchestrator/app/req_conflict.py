import re
from dataclasses import dataclass
from difflib import SequenceMatcher
from typing import Any, Iterable, Optional


_WS_KEYWORDS = {
    "backend": ["backend", "api", "server", "后端", "接口"],
    "web": ["web", "frontend", "ui", "前端", "网页"],
    "ai": ["ai", "agent", "llm", "模型", "推理", "agents", "智能体", "codex"],
    "ios": ["ios", "iphone", "ipad"],
    "android": ["android"],
    "wechat": ["wechat", "小程序", "微信"],
    "data": ["data", "指标", "埋点", "analytics", "数仓", "bi"],
    "devops": ["devops", "deploy", "release", "docker", "compose", "k8s", "运维", "发布"],
}

_MUST = re.compile(r"(必须|只能|默认|务必|需要|should\s+always|must)", re.I)
_MUST_NOT = re.compile(r"(禁止|不得|不允许|严禁|must\s+not|should\s+not)", re.I)
_OPTIONAL = re.compile(r"(可选|可以|允许|支持|建议|may|optional)", re.I)


@dataclass(frozen=True)
class ConflictFinding:
    req_id: str
    topic: str
    existing_stance: str
    new_stance: str
    evidence: str


def _norm(s: str) -> str:
    s = s.strip().lower()
    s = re.sub(r"\s+", " ", s)
    return s


def similarity(a: str, b: str) -> float:
    return SequenceMatcher(None, _norm(a), _norm(b)).ratio()


def infer_workstreams(text: str) -> list[str]:
    t = _norm(text)
    hit: list[str] = []
    for ws, kws in _WS_KEYWORDS.items():
        if any(k.lower() in t for k in kws):
            hit.append(ws)
    return sorted(set(hit)) or ["general"]


def _stance(text: str, *, topic_keywords: list[str]) -> str:
    t = _norm(text)
    kws = [_norm(k) for k in topic_keywords]
    if not any(k in t for k in kws):
        return "UNSPECIFIED"

    # Try to infer stance scoped to the topic by requiring the modal word to be close to
    # the topic keyword. This avoids cross-topic bleeding when a requirement mentions
    # multiple topics (e.g., "must use OAuth; must not use API key").
    #
    # NOTE: We intentionally only match "modal -> keyword" order; it is more reliable for
    # Chinese and avoids false positives like "API key ... 禁止 OAuth" being interpreted
    # as "API key MUST_NOT".
    kw_pat = "|".join(re.escape(k) for k in kws)
    if re.search(rf"({_MUST_NOT.pattern}).{{0,80}}(?:{kw_pat})", t, re.I):
        return "MUST_NOT"
    if re.search(rf"({_MUST.pattern}).{{0,80}}(?:{kw_pat})", t, re.I):
        return "MUST"
    if re.search(rf"({_OPTIONAL.pattern}).{{0,80}}(?:{kw_pat})", t, re.I):
        return "OPTIONAL"
    return "MENTIONED"


_TOPICS = {
    "auth.oauth": ["oauth", "codex login", "chatgpt oauth", "device-auth", "设备码"],
    "auth.api_key": ["api key", "apikey", "openai_api_key", "api_key", "密钥", "key"],
    "network.public_expose": ["0.0.0.0", "公网", "public", "internet", "外网", "对外暴露"],
    "docker.socket": ["/var/run/docker.sock", "docker.sock", "docker socket", "dockersocket"],
}


def detect_duplicate(existing: Iterable[dict[str, Any]], new_text: str, *, threshold: float = 0.92) -> Optional[str]:
    for r in existing:
        rid = str(r.get("req_id", "")).strip()
        text = str(r.get("text", "")).strip()
        if not rid or not text:
            continue
        if similarity(text, new_text) >= threshold:
            return rid
        if _norm(text) and _norm(text) in _norm(new_text):
            return rid
    return None


def detect_conflicts(existing: Iterable[dict[str, Any]], new_text: str) -> list[ConflictFinding]:
    findings: list[ConflictFinding] = []
    for r in existing:
        rid = str(r.get("req_id", "")).strip()
        text = str(r.get("text", "")).strip()
        status = str(r.get("status", "ACTIVE")).strip().upper()
        if not rid or not text or status == "DEPRECATED":
            continue
        for topic, kws in _TOPICS.items():
            s_existing = _stance(text, topic_keywords=kws)
            s_new = _stance(new_text, topic_keywords=kws)
            if s_existing == "UNSPECIFIED" or s_new == "UNSPECIFIED":
                continue
            if (s_existing == "MUST" and s_new == "MUST_NOT") or (s_existing == "MUST_NOT" and s_new == "MUST"):
                findings.append(
                    ConflictFinding(
                        req_id=rid,
                        topic=topic,
                        existing_stance=s_existing,
                        new_stance=s_new,
                        evidence=f"topic={topic}",
                    )
                )
    return findings
