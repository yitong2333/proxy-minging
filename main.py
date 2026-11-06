"""Collect and validate proxy subscriptions shared in configured Telegram channels."""

from __future__ import annotations

import base64
import binascii
import html
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import unquote

import requests
import yaml
from loguru import logger
from tqdm import tqdm


ROOT = Path(__file__).resolve().parent
CONFIG_PATH = ROOT / "config.yaml"
CLASH_OUTPUT_PATH = ROOT / "clash.yaml"
SUBCONVERTER_API = "https://api.asailor.org/sub"
CONVERTER_BATCH_SIZE = 4
CONVERTER_TIMEOUT = (10, 90)
CONVERTER_RETRIES = 2
MAX_WORKERS = 64
MAX_CHANNEL_WORKERS = 16
REQUEST_TIMEOUT = (5, 10)
MAX_RETRIES = 2
MIN_REMAINING_BYTES = 1024**3
USER_AGENT = "clash-verge/v2.0.2"
PROXY_REQUIRED_FIELDS = {
    "ss": {"cipher", "password"},
    "ssr": {"cipher", "password", "protocol", "obfs"},
    "vmess": {"uuid", "cipher"},
    "vless": {"uuid"},
    "trojan": {"password"},
    "hysteria": {"auth-str"},
    "hysteria2": {"password"},
    "hy2": {"password"},
    "tuic": {"uuid", "password"},
    "anytls": {"password"},
    "snell": {"psk", "version"},
}
SUPPORTED_SS_CIPHERS = {
    "aes-128-ctr", "aes-192-ctr", "aes-256-ctr",
    "aes-128-cfb", "aes-192-cfb", "aes-256-cfb",
    "aes-128-gcm", "aes-192-gcm", "aes-256-gcm",
    "aes-128-ccm", "aes-192-ccm", "aes-256-ccm",
    "aes-128-gcm-siv", "aes-256-gcm-siv",
    "chacha20-ietf", "chacha20", "xchacha20",
    "chacha20-ietf-poly1305", "xchacha20-ietf-poly1305",
    "chacha8-ietf-poly1305", "xchacha8-ietf-poly1305",
    "2022-blake3-aes-128-gcm", "2022-blake3-aes-256-gcm",
    "2022-blake3-chacha20-poly1305",
    "lea-128-gcm", "lea-192-gcm", "lea-256-gcm", "rabbit128-poly1305",
}
PROXY_ALLOWED_VALUES = {
    "vmess": {"cipher": {"auto", "none", "zero", "aes-128-gcm", "chacha20-poly1305"}},
    "vless": {"flow": {"xtls-rprx-vision", "xtls-rprx-vision-udp443"}},
    "hysteria2": {"obfs": {"", "salamander", "gecko"}},
    "hy2": {"obfs": {"", "salamander", "gecko"}},
}
FLAG_COUNTRY_NAMES = {
    "US": "美国", "GB": "英国", "JP": "日本", "KR": "韩国", "HK": "香港",
    "TW": "台湾", "CN": "中国", "SG": "新加坡", "DE": "德国", "FR": "法国",
    "NL": "荷兰", "IT": "意大利", "CA": "加拿大", "RU": "俄罗斯", "ES": "西班牙",
    "SE": "瑞典", "CH": "瑞士", "FI": "芬兰", "NO": "挪威", "AU": "澳大利亚",
    "ID": "印度尼西亚", "TR": "土耳其", "UA": "乌克兰", "BR": "巴西", "IN": "印度",
    "AE": "阿联酋", "IR": "伊朗", "PL": "波兰", "CZ": "捷克", "RO": "罗马尼亚",
    "AT": "奥地利", "BE": "比利时", "DK": "丹麦", "TH": "泰国", "VN": "越南",
    "MX": "墨西哥", "ZA": "南非", "IL": "以色列", "AR": "阿根廷", "EG": "埃及",
    "IS": "冰岛", "PT": "葡萄牙", "IE": "爱尔兰", "RS": "塞尔维亚", "BG": "保加利亚",
    "EE": "爱沙尼亚", "LT": "立陶宛", "LV": "拉脱维亚", "HU": "匈牙利",
    "NP": "尼泊尔", "KH": "柬埔寨", "MY": "马来西亚", "PH": "菲律宾", "PK": "巴基斯坦",
    "UZ": "乌兹别克斯坦", "AZ": "阿塞拜疆", "AL": "阿尔巴尼亚", "BA": "波黑",
    "VE": "委内瑞拉", "EC": "厄瓜多尔", "CM": "喀麦隆", "CY": "塞浦路斯",
    "SC": "塞舌尔", "BZ": "伯利兹", "DO": "多米尼加", "SK": "斯洛伐克",
    "AM": "亚美尼亚", "KZ": "哈萨克斯坦", "RS": "塞尔维亚", "SI": "斯洛文尼亚",
    "CR": "哥斯达黎加",
}
COUNTRY_ALIASES = {
    "us": "美国", "usa": "美国", "united states": "美国",
    "united states of america": "美国", "uk": "英国", "gb": "英国",
    "great britain": "英国", "united kingdom": "英国", "south korea": "韩国",
    "korea": "韩国", "hong kong": "香港", "taiwan": "台湾", "china": "中国",
    "singapore": "新加坡", "germany": "德国", "france": "法国", "netherlands": "荷兰",
    "the netherlands": "荷兰", "holland": "荷兰", "italy": "意大利", "canada": "加拿大",
    "russia": "俄罗斯", "spain": "西班牙", "sweden": "瑞典", "switzerland": "瑞士",
    "finland": "芬兰", "norway": "挪威", "australia": "澳大利亚",
    "indonesia": "印度尼西亚", "turkey": "土耳其", "türkiye": "土耳其",
    "ukraine": "乌克兰", "brazil": "巴西", "india": "印度", "uae": "阿联酋",
    "united arab emirates": "阿联酋", "iran": "伊朗", "poland": "波兰",
    "czechia": "捷克", "czech republic": "捷克", "romania": "罗马尼亚",
    "austria": "奥地利", "belgium": "比利时", "denmark": "丹麦", "thailand": "泰国",
    "vietnam": "越南", "mexico": "墨西哥", "south africa": "南非", "israel": "以色列",
    "argentina": "阿根廷", "egypt": "埃及", "iceland": "冰岛", "portugal": "葡萄牙",
    "ireland": "爱尔兰", "serbia": "塞尔维亚", "bulgaria": "保加利亚",
    "estonia": "爱沙尼亚", "lithuania": "立陶宛", "latvia": "拉脱维亚",
    "hungary": "匈牙利", "nepal": "尼泊尔", "cambodia": "柬埔寨",
    "malaysia": "马来西亚", "philippines": "菲律宾", "pakistan": "巴基斯坦",
    "uzbekistan": "乌兹别克斯坦", "azerbaijan": "阿塞拜疆", "albania": "阿尔巴尼亚",
    "bosnia": "波黑", "bosnia and herzegovina": "波黑", "venezuela": "委内瑞拉",
    "ecuador": "厄瓜多尔", "cameroon": "喀麦隆", "cyprus": "塞浦路斯",
    "seychelles": "塞舌尔", "belize": "伯利兹", "dominican republic": "多米尼加",
    "slovakia": "斯洛伐克", "armenia": "亚美尼亚", "kazakhstan": "哈萨克斯坦",
    "slovenia": "斯洛文尼亚", "chile": "智利", "japan": "日本",
    "costa rica": "哥斯达黎加", "greece": "希腊", "new zealand": "新西兰",
    "cuba": "古巴", "iraq": "伊拉克", "yemen": "也门", "qatar": "卡塔尔",
    "luxembourg": "卢森堡", "greenland": "格陵兰", "macau": "澳门",
}

URL_PATTERN = re.compile(
    r"https?://[-A-Za-z0-9+&@#/%?=~_|!:,.;]+[-A-Za-z0-9+&@#/%=~_|]"
)
NODE_PATTERN = re.compile(
    r"(?:vmess|vless|ss|ssr|trojan|hysteria2?|hy2)://[^\s<>\"']+",
    re.IGNORECASE,
)
NODE_SCHEMES = ("ss://", "ssr://", "vmess://", "trojan://", "vless://")
COUNTRY_FLAG_PATTERN = re.compile(r"[\U0001F1E6-\U0001F1FF]{2}")
ALLOW_URL_PARTS = (
    "sub",
    "clash",
    "paste",
    "tt.vg",
    "shz.al",
    "proxies",
    "raw.githubusercontent.com",
)
_thread_local = threading.local()


def get_session() -> requests.Session:
    """Reuse HTTP connections within each worker thread."""
    session = getattr(_thread_local, "session", None)
    if session is None:
        session = requests.Session()
        _thread_local.session = session
    return session


def load_channels(config_path: Path = CONFIG_PATH) -> list[str]:
    """Load Telegram channel names and convert them to their public web pages."""
    try:
        with config_path.open(encoding="utf-8") as config_file:
            config: Any = yaml.safe_load(config_file)
    except (OSError, yaml.YAMLError) as exc:
        raise RuntimeError(f"无法读取配置文件 {config_path}: {exc}") from exc

    channels = config.get("tgchannel") if isinstance(config, dict) else None
    if not isinstance(channels, list) or not all(
        isinstance(channel, str) and channel.strip() for channel in channels
    ):
        raise ValueError("config.yaml 中的 tgchannel 必须是非空字符串列表")

    result = []
    for channel in channels:
        name = channel.strip().rstrip("/").split("/")[-1]
        if name:
            result.append(f"https://t.me/s/{name}")
    return result


def fetch_channel(channel_url: str) -> tuple[list[str], list[str]]:
    """Fetch a Telegram preview page and extract subscription and node URLs."""
    try:
        response = get_session().get(channel_url, timeout=REQUEST_TIMEOUT)
        response.raise_for_status()
    except requests.RequestException as exc:
        logger.warning("获取频道失败 {}: {}", channel_url, exc)
        return [], []

    page = html.unescape(response.text)
    urls = URL_PATTERN.findall(page)
    nodes = NODE_PATTERN.findall(page)
    logger.info("频道获取完成 {}（{} 个链接，{} 个节点）", channel_url, len(urls), len(nodes))
    return urls, nodes


def has_remaining_traffic(info: str) -> bool | None:
    """Return whether a subscription has at least 1 GiB left; None if unknown."""
    fields = dict(re.findall(r"(upload|download|total|expire)=(\d+)", info))
    if not {"upload", "download", "total"}.issubset(fields):
        return None

    now = datetime.now().timestamp()
    expire = int(fields.get("expire", "0"))
    if expire and expire > 10**12:
        expire //= 1000
    if expire and expire <= now:
        return False

    remaining = int(fields["total"]) - int(fields["upload"]) - int(fields["download"])
    return remaining > MIN_REMAINING_BYTES


def decode_subscription(text: str) -> str:
    """Decode a base64 subscription body, accepting URL-safe and missing padding."""
    compact = re.sub(r"\s+", "", text)
    padded = compact + "=" * (-len(compact) % 4)
    try:
        decoded = base64.urlsafe_b64decode(padded)
        return decoded.decode("utf-8", errors="replace")
    except (binascii.Error, ValueError):
        return ""


def looks_like_clash(text: str) -> bool:
    return re.search(r"(?m)^\s*proxies\s*:", text) is not None


def looks_like_node_subscription(text: str) -> bool:
    decoded = decode_subscription(text[:200_000])
    sample = decoded or text
    return any(scheme in sample.lower() for scheme in NODE_SCHEMES)


def check_subscription(url: str) -> str | None:
    """Check one candidate URL and return its output category, if recognized."""
    headers = {"User-Agent": USER_AGENT}
    response: requests.Response | None = None
    for attempt in range(MAX_RETRIES):
        try:
            response = get_session().get(url, headers=headers, timeout=REQUEST_TIMEOUT)
            response.raise_for_status()
            break
        except requests.RequestException as exc:
            if attempt + 1 == MAX_RETRIES:
                status_code = getattr(getattr(exc, "response", None), "status_code", None)
                logger.debug(
                    "订阅不可用（{}，HTTP {}）",
                    type(exc).__name__,
                    status_code or "n/a",
                )
                return None
            time.sleep(0.25 * (attempt + 1))

    assert response is not None
    traffic_info = response.headers.get("subscription-userinfo", "")
    if traffic_info:
        has_traffic = has_remaining_traffic(traffic_info)
        if has_traffic is not None:
            if not has_traffic:
                logger.debug("订阅已过期或剩余流量不足 1 GiB: {}", url)
                return None
            return "机场订阅"

    if looks_like_clash(response.text):
        return "clash订阅"
    if looks_like_node_subscription(response.text):
        return "v2订阅"
    return None


def write_text(path: Path, lines: list[str]) -> None:
    """Write a newline-delimited output file with a stable trailing newline."""
    content = "\n".join(lines)
    if lines:
        content += "\n"
    path.write_text(content, encoding="utf-8")


def write_outputs(
    categories: dict[str, set[str]], candidate_urls: set[str], nodes: set[str]
) -> None:
    latest_path = ROOT / "latest.yaml"
    latest_path.write_text(
        yaml.safe_dump(
            {name: sorted(urls) for name, urls in categories.items()},
            allow_unicode=True,
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    write_text(ROOT / "url.txt", sorted(candidate_urls))
    write_text(ROOT / "v2ray.txt", sorted(unquote(node) for node in nodes))


def parse_converter_proxies(content: str) -> list[dict[str, Any]]:
    """Extract proxy nodes from a SubConverter Clash list response."""
    try:
        config = yaml.safe_load(content)
    except yaml.YAMLError as exc:
        raise ValueError("转换 API 返回的内容不是有效 YAML") from exc
    if not isinstance(config, dict):
        raise ValueError("转换 API 没有返回 YAML 配置对象")

    proxies = config.get("proxies")
    if not isinstance(proxies, list):
        raise ValueError("转换结果中没有 proxies 节点列表")
    candidates = [
        proxy
        for proxy in proxies
        if isinstance(proxy, dict) and isinstance(proxy.get("name"), str)
    ]
    valid_proxies = [proxy for proxy in candidates if is_valid_proxy(proxy)]
    invalid_count = len(candidates) - len(valid_proxies)
    if invalid_count:
        logger.warning("过滤了 {} 个字段缺失、值不受支持或配置不完整的代理节点", invalid_count)
    if not valid_proxies:
        raise ValueError("转换结果中没有有效代理节点")
    return valid_proxies


def is_valid_proxy(proxy: dict[str, Any]) -> bool:
    """Reject nodes missing protocol fields or incompatible transport settings."""
    proxy_type = str(proxy.get("type", "")).lower()
    if "reality-opts" in proxy and proxy["reality-opts"] is not None:
        reality_opts = proxy["reality-opts"]
        if not isinstance(reality_opts, dict):
            return False
        if reality_opts:
            if proxy_type != "vless" or proxy.get("tls") is not True:
                return False
            if not is_valid_reality_public_key(reality_opts.get("public-key")):
                return False
            short_id = reality_opts.get("short-id", "")
            if not isinstance(short_id, str) or not re.fullmatch(
                r"(?:[0-9a-fA-F]{2}){0,8}", short_id
            ):
                return False

    required = {"server", "port"} | PROXY_REQUIRED_FIELDS.get(proxy_type, set())
    for field in required:
        value = proxy.get(field)
        if field in {"auth-str", "auth_str"}:
            value = proxy.get("auth-str") or proxy.get("auth_str")
        if value is None or value == "":
            return False
        if isinstance(value, str) and value.strip().lower() in {"none", "null"}:
            return False

    port = proxy.get("port")
    if isinstance(port, bool) or not isinstance(port, int) or not 1 <= port <= 65535:
        return False
    server = proxy.get("server")
    if not isinstance(server, str) or not server.strip() or server != server.strip():
        return False

    cipher = proxy.get("cipher")
    if proxy_type == "ss" and cipher not in SUPPORTED_SS_CIPHERS:
        return False
    for field, supported_values in PROXY_ALLOWED_VALUES.get(proxy_type, {}).items():
        value = proxy.get(field)
        if value is not None and value not in supported_values:
            return False
    return True


def is_valid_reality_public_key(value: Any) -> bool:
    """Accept only Mihomo's unpadded base64url encoding of a 32-byte X25519 key."""
    if not isinstance(value, str):
        return False
    key = value
    if not re.fullmatch(r"[A-Za-z0-9_-]{43}", key):
        return False
    try:
        decoded = base64.b64decode(
            key + "=" * (-len(key) % 4), altchars=b"-_", validate=True
        )
    except (binascii.Error, ValueError):
        return False
    if len(decoded) != 32:
        return False
    canonical_urlsafe = base64.urlsafe_b64encode(decoded).decode("ascii").rstrip("=")
    return key == canonical_urlsafe


def country_from_proxy_name(name: str) -> str:
    """Infer a country label from a flag or recognized country name."""
    known_countries = set(FLAG_COUNTRY_NAMES.values()) | {
        "波斯尼亚和黑塞哥维那", "智利", "哥斯达黎加", "希腊", "新西兰",
        "古巴", "伊拉克", "也门", "卡塔尔", "卢森堡", "格陵兰", "澳门",
    }

    flag_match = COUNTRY_FLAG_PATTERN.search(name)
    if flag_match:
        code = "".join(
            chr(ord(char) - 0x1F1E6 + ord("A")) for char in flag_match.group(0)
        )
        if code in FLAG_COUNTRY_NAMES:
            return FLAG_COUNTRY_NAMES[code]
        tail = name[flag_match.end() :]
        chinese_labels = [
            country
            for country in known_countries
            if country in tail
        ]
        if chinese_labels:
            return max(chinese_labels, key=len)
        english_label = re.split(r"[_|/·-]", tail.strip(" _-|/·"), maxsplit=1)[0].strip()
        if english_label and re.fullmatch(r"[A-Za-z][A-Za-z .'-]*", english_label):
            return COUNTRY_ALIASES.get(english_label.lower(), "未知地区")
        return "未知地区"

    chinese_labels = [country for country in known_countries if country in name]
    if chinese_labels:
        return max(chinese_labels, key=len)

    prefix = re.split(r"[_|/·]", name, maxsplit=1)[0].strip()
    if prefix and re.fullmatch(r"[A-Za-z][A-Za-z .'-]*", prefix):
        if prefix.lower() in {"unknown", "other", "none"}:
            return "未知地区"
        return COUNTRY_ALIASES.get(prefix.lower(), "未知地区")
    return "未知地区"


def normalize_clash_config(proxies: list[dict[str, Any]]) -> str:
    """Build one Clash profile from converted nodes with unique names."""
    valid_proxies = [proxy for proxy in proxies if is_valid_proxy(proxy)]
    invalid_count = len(proxies) - len(valid_proxies)
    if invalid_count:
        logger.warning("生成配置前过滤了 {} 个字段缺失、值不受支持或配置不完整的节点", invalid_count)

    unique_proxies: list[dict[str, Any]] = []
    seen_nodes: set[str] = set()
    used_names: set[str] = set()

    for original in valid_proxies:
        proxy = dict(original)
        identity = dict(proxy)
        identity.pop("name", None)
        fingerprint = yaml.safe_dump(identity, allow_unicode=True, sort_keys=True)
        if fingerprint in seen_nodes:
            continue
        seen_nodes.add(fingerprint)

        base_name = proxy["name"].strip() or "Proxy"
        name = base_name
        suffix = 2
        while name in used_names:
            name = f"{base_name} ({suffix})"
            suffix += 1
        proxy["name"] = name
        used_names.add(name)
        unique_proxies.append(proxy)

    if not unique_proxies:
        raise ValueError("去重后没有可用代理节点")

    country_nodes: dict[str, list[str]] = {}
    for proxy in unique_proxies:
        country = country_from_proxy_name(proxy["name"])
        country_nodes.setdefault(country, []).append(proxy["name"])

    country_group_names = {
        country: f"国家 - {country}" for country in sorted(country_nodes)
    }
    proxy_groups = [
        {
            "name": "Proxy",
            "type": "select",
            "proxies": list(country_group_names.values()) + ["DIRECT"],
        }
    ]
    proxy_groups.extend(
        {
            "name": country_group_names[country],
            "type": "select",
            "proxies": sorted(names),
        }
        for country, names in sorted(country_nodes.items())
    )

    config = {
        "proxies": unique_proxies,
        "proxy-groups": proxy_groups,
        "rules": ["MATCH,Proxy"],
    }
    return yaml.safe_dump(config, allow_unicode=True, sort_keys=False)


def convert_batch(subscriptions: list[str]) -> tuple[list[dict[str, Any]], int]:
    """Convert one batch, recursively splitting it when the public API times out."""
    params = {
        "target": "clash",
        "list": "true",
        "url": "|".join(subscriptions),
    }
    proxies: list[dict[str, Any]] | None = None
    error_type = "UnknownError"
    status_code: int | None = None

    for attempt in range(CONVERTER_RETRIES):
        try:
            response = requests.get(
                SUBCONVERTER_API,
                params=params,
                headers={"User-Agent": USER_AGENT},
                timeout=CONVERTER_TIMEOUT,
            )
            response.raise_for_status()
            if "html" in response.headers.get("Content-Type", "").lower():
                raise ValueError("HTML response")
            proxies = parse_converter_proxies(response.text)
            break
        except requests.RequestException as exc:
            error_type = type(exc).__name__
            status_code = getattr(getattr(exc, "response", None), "status_code", None)
            # A 504 will not improve by retrying the same large input. Split it now.
            if status_code == 504 or isinstance(exc, requests.ReadTimeout):
                break
        except ValueError as exc:
            error_type = type(exc).__name__
            break

        if attempt + 1 < CONVERTER_RETRIES:
            time.sleep(1.0 * (attempt + 1))

    if proxies is not None:
        return proxies, 0

    if len(subscriptions) > 1:
        logger.warning(
            "转换批次失败（{} 个来源，{}，HTTP {}），拆分后继续",
            len(subscriptions),
            error_type,
            status_code or "n/a",
        )
        midpoint = len(subscriptions) // 2
        left_proxies, left_failed = convert_batch(subscriptions[:midpoint])
        right_proxies, right_failed = convert_batch(subscriptions[midpoint:])
        return left_proxies + right_proxies, left_failed + right_failed

    logger.warning(
        "跳过无法转换的订阅（{}，HTTP {}）",
        error_type,
        status_code or "n/a",
    )
    return [], 1


def convert_subscriptions(subscriptions: set[str]) -> bool:
    """Convert the current subscriptions and atomically publish the Clash profile."""
    if not subscriptions:
        logger.warning("没有通过筛选的订阅，保留现有 {}", CLASH_OUTPUT_PATH.name)
        return False

    ordered_subscriptions = sorted(subscriptions)
    batches = [
        ordered_subscriptions[index : index + CONVERTER_BATCH_SIZE]
        for index in range(0, len(ordered_subscriptions), CONVERTER_BATCH_SIZE)
    ]
    converted_proxies: list[dict[str, Any]] = []
    failed_subscriptions = 0
    for batch_number, batch in enumerate(batches, start=1):
        logger.info("转换订阅批次 {}/{}（{} 个来源）", batch_number, len(batches), len(batch))
        proxies, failed = convert_batch(batch)
        converted_proxies.extend(proxies)
        failed_subscriptions += failed

    if not converted_proxies:
        logger.error("所有订阅转换均失败，保留现有 {}", CLASH_OUTPUT_PATH.name)
        return False

    temp_path = CLASH_OUTPUT_PATH.with_suffix(".yaml.tmp")
    try:
        config_text = normalize_clash_config(converted_proxies)
        temp_path.write_text(config_text, encoding="utf-8")
        temp_path.replace(CLASH_OUTPUT_PATH)
    except (ValueError, OSError) as exc:
        temp_path.unlink(missing_ok=True)
        logger.error("无法写入新的 {}（{}），保留现有配置", CLASH_OUTPUT_PATH.name, type(exc).__name__)
        return False

    logger.info(
        "已生成 {}（输入 {} 个订阅，{} 个节点，{} 个订阅转换失败，{} 字节）",
        CLASH_OUTPUT_PATH.name,
        len(subscriptions),
        len(converted_proxies),
        failed_subscriptions,
        CLASH_OUTPUT_PATH.stat().st_size,
    )
    return True


def main() -> None:
    channels = load_channels()
    logger.info("读取到 {} 个 Telegram 频道", len(channels))

    candidate_urls: set[str] = set()
    nodes: set[str] = set()
    with ThreadPoolExecutor(max_workers=MAX_CHANNEL_WORKERS) as executor:
        futures = {executor.submit(fetch_channel, channel): channel for channel in channels}
        for future in tqdm(as_completed(futures), total=len(futures), desc="抓取频道"):
            channel_url = futures[future]
            try:
                urls, channel_nodes = future.result()
            except Exception:
                logger.exception("处理频道时发生未处理异常: {}", channel_url)
                continue
            candidate_urls.update(
                url
                for url in urls
                if any(part in url for part in ALLOW_URL_PARTS)
                and "https://t.me/" not in url
            )
            nodes.update(channel_nodes)

    categories: dict[str, set[str]] = {
        "机场订阅": set(),
        "clash订阅": set(),
        "v2订阅": set(),
    }
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
        futures = {
            executor.submit(check_subscription, url): url for url in candidate_urls
        }
        for future in tqdm(as_completed(futures), total=len(futures), desc="筛选订阅"):
            try:
                category = future.result()
            except Exception as exc:
                # Subscription URLs can contain credentials; never include them in Actions logs.
                logger.error("筛选订阅时发生未处理异常（{}）", type(exc).__name__)
                continue
            if category:
                categories[category].add(futures[future])

    write_outputs(categories, candidate_urls, nodes)
    convert_subscriptions(set().union(*categories.values()))
    logger.info(
        "完成：候选订阅 {} 个，机场 {} 个，Clash {} 个，节点 {} 个",
        len(candidate_urls),
        len(categories["机场订阅"]),
        len(categories["clash订阅"]),
        len(nodes),
    )


if __name__ == "__main__":
    main()
