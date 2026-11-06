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
MAX_WORKERS = 64
MAX_CHANNEL_WORKERS = 16
REQUEST_TIMEOUT = (5, 10)
MAX_RETRIES = 2
MIN_REMAINING_BYTES = 1024**3
USER_AGENT = "clash-verge/v2.0.2"

URL_PATTERN = re.compile(
    r"https?://[-A-Za-z0-9+&@#/%?=~_|!:,.;]+[-A-Za-z0-9+&@#/%=~_|]"
)
NODE_PATTERN = re.compile(
    r"(?:vmess|vless|ss|ssr|trojan|hysteria2?|hy2)://[^\s<>\"']+",
    re.IGNORECASE,
)
NODE_SCHEMES = ("ss://", "ssr://", "vmess://", "trojan://", "vless://")
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
                logger.debug("订阅不可用 {}: {}", url, exc)
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
            url = futures[future]
            try:
                category = future.result()
            except Exception:
                logger.exception("筛选订阅时发生未处理异常: {}", url)
                continue
            if category:
                categories[category].add(url)

    write_outputs(categories, candidate_urls, nodes)
    logger.info(
        "完成：候选订阅 {} 个，机场 {} 个，Clash {} 个，节点 {} 个",
        len(candidate_urls),
        len(categories["机场订阅"]),
        len(categories["clash订阅"]),
        len(nodes),
    )


if __name__ == "__main__":
    main()
