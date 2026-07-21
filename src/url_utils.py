from __future__ import annotations

import ipaddress
import re
from html import unescape
from dataclasses import dataclass
from urllib.parse import unquote, urljoin, urlsplit, urlunsplit


FULL_URL = "완전한 URL"
PROTOCOL_RELATIVE = "프로토콜 상대"
INTERNAL_ABSOLUTE_PATH = "내부 절대경로"
INTERNAL_RELATIVE_PATH = "내부 상대경로"
BROWSER_RESOLVED = "브라우저 URL 해석"
EMBEDDED_EXTERNAL_DOMAIN_DETAIL = "BigKinds 경로 내부에 외부 도메인 문자열이 포함됨"

_COMMON_TLDS = {
    "app", "asia", "biz", "com", "dev", "edu", "gov", "info", "io", "me",
    "kr", "mil", "mobi", "name", "net", "news", "online", "org", "pro", "site",
    "store", "tech", "tv", "xyz",
}
_HOST_LABEL_RE = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$", re.IGNORECASE)


@dataclass(frozen=True)
class NormalizedUrl:
    value: str
    method: str
    input_value: str


@dataclass(frozen=True)
class UrlStructureAnalysis:
    anomalous: bool = False
    details: str = ""
    inferred_url: str = ""


def _host_from_domain_reference(value: str) -> str:
    authority = re.split(r"[/\\?#]", value, maxsplit=1)[0]
    if not authority or "@" in authority:
        return ""
    if authority.startswith("["):
        closing = authority.find("]")
        return authority[1:closing] if closing > 0 else ""
    return authority.rsplit(":", 1)[0] if authority.count(":") == 1 else authority


def looks_like_domain_reference(value: str) -> bool:
    """프로토콜만 빠진 외부 도메인형 URL을 보수적으로 식별한다."""
    candidate = (value or "").strip()
    if not candidate or candidate.startswith(("/", ".")) or re.search(r"\s", candidate):
        return False
    host = _host_from_domain_reference(candidate).lower().rstrip(".")
    if not host:
        return False
    try:
        ipaddress.ip_address(host)
        return True
    except ValueError:
        pass
    if host == "localhost":
        return True
    labels = host.split(".")
    if len(labels) < 2 or not all(_HOST_LABEL_RE.fullmatch(label) for label in labels):
        return False
    suffix = labels[-1]
    return (
        host.startswith("www.")
        or suffix.startswith("xn--")
        or suffix in _COMMON_TLDS
        or len(suffix) == 2
        or len(labels) >= 3
    )


def _canonicalize_http_url(absolute: str, *, keep_fragment: bool = False) -> str:
    parts = urlsplit(absolute)
    scheme = parts.scheme.lower()
    if scheme not in {"http", "https"} or not parts.hostname:
        return ""
    hostname = (parts.hostname or "").lower()
    try:
        port = parts.port
    except ValueError:
        return ""
    default_port = (scheme == "http" and port == 80) or (scheme == "https" and port == 443)
    netloc = f"[{hostname}]" if ":" in hostname else hostname
    if port and not default_port:
        netloc += f":{port}"
    fragment = parts.fragment if keep_fragment else ""
    return urlunsplit((scheme, netloc, parts.path or "", parts.query, fragment))


def decode_html_url_entities(url: str) -> str:
    """URL 입력 경계에서 HTML 엔티티를 정확히 한 번 해제한다."""
    return unescape(str(url or "")).strip()


def resolve_url_reference(url: str, base_url: str) -> str:
    """브라우저의 ``new URL(value, base)``와 같은 상대 URL 해석만 수행한다.

    도메인처럼 보이는 상대 문자열에도 스킴을 추정해 붙이지 않는다. 예를 들어
    ``www.example.com/a``는 현재 문서 경로 아래의 상대 URL로 유지된다.
    """
    value = decode_html_url_entities(url)
    base = str(base_url or "").strip()
    if not value or not base:
        return ""
    absolute = urljoin(base, value)
    return _canonicalize_http_url(absolute, keep_fragment=True)


def normalize_url_with_method(
    url: str, base_url: str | None = None, *, allow_internal_relative: bool = True,
) -> NormalizedUrl:
    raw = str(url or "")
    value = decode_html_url_entities(raw)
    if not value:
        return NormalizedUrl("", "", value)

    if re.match(r"(?i)^https?://", value):
        absolute, method = value, FULL_URL
    elif value.startswith("//"):
        if not base_url:
            return NormalizedUrl("", PROTOCOL_RELATIVE, value)
        absolute, method = urljoin(base_url, value), PROTOCOL_RELATIVE
    elif value.startswith("/"):
        absolute, method = urljoin(base_url or "", value), INTERNAL_ABSOLUTE_PATH
    elif allow_internal_relative and base_url:
        absolute, method = urljoin(base_url, value), INTERNAL_RELATIVE_PATH
    else:
        return NormalizedUrl("", INTERNAL_RELATIVE_PATH, value)
    return NormalizedUrl(_canonicalize_http_url(absolute), method, value)


def normalize_url(url: str, base_url: str | None = None) -> str:
    return normalize_url_with_method(url, base_url).value


def infer_external_url(url: str) -> str:
    """프로토콜 없는 도메인형 문자열의 추정 URL을 진단용으로만 만든다."""
    value = decode_html_url_entities(url)
    if not looks_like_domain_reference(value):
        return ""
    return _canonicalize_http_url(f"https://{value}", keep_fragment=True)


def analyze_url_structure(
    url: str, site_domain: str = "bigkinds.or.kr",
) -> UrlStructureAnalysis:
    """BigKinds ``/regional/<외부도메인>/...`` 구조를 수정하지 않고 진단한다."""
    parts = urlsplit(url or "")
    hostname = (parts.hostname or "").lower().rstrip(".")
    trusted = site_domain.lower().rstrip(".")
    if not hostname or not (hostname == trusted or hostname.endswith(f".{trusted}")):
        return UrlStructureAnalysis()

    segments = [unquote(segment) for segment in parts.path.split("/") if segment]
    for index, segment in enumerate(segments[:-1]):
        if segment.lower() != "regional":
            continue
        embedded = segments[index + 1]
        embedded_host = _host_from_domain_reference(embedded).lower().rstrip(".")
        labels = embedded_host.split(".")
        looks_embedded_external = (
            looks_like_domain_reference(embedded)
            and (
                embedded_host.startswith("www.")
                or len(labels) >= 3
                or (len(labels) >= 2 and labels[-1] in _COMMON_TLDS)
            )
        )
        if not looks_embedded_external:
            continue
        remainder = "/".join(segments[index + 2:])
        inferred_path = f"/{remainder}" if remainder else ""
        inferred = urlunsplit(("https", embedded, inferred_path, parts.query, parts.fragment))
        return UrlStructureAnalysis(True, EMBEDDED_EXTERNAL_DOMAIN_DETAIL, inferred)
    return UrlStructureAnalysis()


def is_suspicious_embedded_external_url(url: str, site_domain: str = "bigkinds.or.kr") -> bool:
    """BigKinds 내부 경로에 외부 도메인 문자열이 경로 조각으로 박힌 경우를 찾는다."""
    return analyze_url_structure(url, site_domain).anomalous


def result_dedup_key(requested_date: str, region: str, issue_order: int, original_url: str) -> tuple[str, str, int, str]:
    return requested_date, region, issue_order, normalize_url(original_url)


def deduplicate_rows(rows):
    seen = set()
    result = []
    for row in rows:
        url = getattr(row, "original_url", "")
        fallback = getattr(row, "article_title", "") if not url else ""
        key = result_dedup_key(row.requested_date, row.region, row.issue_order, url) + (fallback,)
        if key not in seen:
            seen.add(key)
            result.append(row)
    return result
