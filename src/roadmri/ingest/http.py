"""共通 HTTP クライアント（§5.6）。User-Agent、ホスト別レート制限、指数バックオフ、プロキシ・社内 CA、キャッシュ。

外部アクセスはすべてこのモジュールを経由する。
"""

from __future__ import annotations

import os
import threading
import time
from collections import defaultdict
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import httpx

from roadmri import __version__
from roadmri.logging import get_logger, log_event

log = get_logger("http")


class OfflineError(RuntimeError):
    """オフラインモードでキャッシュにないリソースを要求した。"""


class NotFoundError(RuntimeError):
    pass


class _HostLimiter:
    def __init__(
        self, min_interval_s: float = 0.0, max_concurrency: int = 4, per_second: float | None = None
    ):
        self.sem = threading.BoundedSemaphore(max(1, max_concurrency))
        interval = min_interval_s
        if per_second:
            interval = max(interval, 1.0 / float(per_second))
        self.interval = interval
        self.lock = threading.Lock()
        self.next_time = 0.0

    def acquire(self) -> None:
        self.sem.acquire()
        with self.lock:
            now = time.monotonic()
            wait = self.next_time - now
            self.next_time = max(now, self.next_time) + self.interval
        if wait > 0:
            time.sleep(wait)

    def release(self) -> None:
        self.sem.release()


class HttpClient:
    def __init__(self, settings: dict[str, Any], offline: bool = False):
        hs = settings.get("http", {})
        self.offline = offline or os.environ.get("ROADMRI_OFFLINE") == "1"
        self.timeout = float(hs.get("timeout_s", 180))
        self.max_retries = int(hs.get("max_retries", 4))
        self.backoff = float(hs.get("backoff_base_s", 2.0))
        self.retry_after_cap_s = float(hs.get("retry_after_cap_s", 120.0))
        self.user_agent = f"roadmri/{__version__} {hs.get('user_agent_suffix', '')}".strip()
        ca = hs.get("ca_bundle") or os.environ.get("REQUESTS_CA_BUNDLE") or os.environ.get("SSL_CERT_FILE")
        self.verify: str | bool = ca if ca else True
        self._limits_cfg: dict[str, dict[str, Any]] = hs.get("rate_limits", {}) or {}
        self._limiters: dict[str, _HostLimiter] = {}
        self._lock = threading.Lock()
        self._client: httpx.Client | None = None
        self.stats: dict[str, int] = defaultdict(int)

    def _limiter(self, host: str) -> _HostLimiter:
        with self._lock:
            if host not in self._limiters:
                c = self._limits_cfg.get(host) or self._limits_cfg.get("default") or {}
                self._limiters[host] = _HostLimiter(
                    float(c.get("min_interval_s", 0.0)), int(c.get("max_concurrency", 4)), c.get("per_second")
                )
            return self._limiters[host]

    @property
    def client(self) -> httpx.Client:
        if self._client is None:
            self._client = httpx.Client(
                timeout=self.timeout,
                headers={"User-Agent": self.user_agent},
                verify=self.verify,
                trust_env=True,  # HTTPS_PROXY / HTTP_PROXY を尊重
                follow_redirects=True,
            )
        return self._client

    def _host_cfg(self, host: str) -> dict[str, Any]:
        return self._limits_cfg.get(host) or self._limits_cfg.get("default") or {}

    def host_timeout(self, host: str) -> float:
        """ホスト別のタイムアウト（rate_limits.<host>.timeout_s、なければ http.timeout_s）。"""
        return float(self._host_cfg(host).get("timeout_s", self.timeout))

    def host_max_concurrency(self, url: str) -> int:
        return int(self._host_cfg(urlparse(url).netloc).get("max_concurrency", 4))

    @staticmethod
    def _retry_after(r: httpx.Response | None, cap_s: float) -> float:
        if r is None:
            return 0.0
        v = r.headers.get("Retry-After", "")
        try:
            return min(max(float(v), 0.0), cap_s)
        except ValueError:
            return 0.0

    def request(self, method: str, url: str, **kw: Any) -> httpx.Response:
        if self.offline:
            raise OfflineError(f"オフラインモードのため取得できません: {url}")
        host = urlparse(url).netloc
        lim = self._limiter(host)
        hc = self._host_cfg(host)
        retries = int(hc.get("max_retries", self.max_retries))
        kw.setdefault("timeout", self.host_timeout(host))
        last_exc: Exception | None = None
        for attempt in range(retries + 1):
            lim.acquire()
            try:
                r = self.client.request(method, url, **kw)
                self.stats[host] += 1
            except httpx.HTTPError as e:
                last_exc = e
                r = None
            finally:
                lim.release()
            if r is not None:
                if r.status_code == 404:
                    raise NotFoundError(url)
                if r.status_code < 400:
                    return r
                if r.status_code not in (429, 500, 502, 503, 504):
                    r.raise_for_status()
                last_exc = httpx.HTTPStatusError(f"HTTP {r.status_code}", request=r.request, response=r)
            if attempt < retries:
                delay = max(self.backoff * (2**attempt), self._retry_after(r, self.retry_after_cap_s))
                log_event(log, "retry", url=url, attempt=attempt + 1, delay_s=delay, error=str(last_exc))
                time.sleep(delay)
        assert last_exc is not None
        raise last_exc

    def get_bytes(self, url: str, **kw: Any) -> bytes:
        return self.request("GET", url, **kw).content

    def download(self, url: str, dest: Path, force: bool = False) -> Path:
        """dest に保存（既にあれば取得しない）。"""
        if dest.exists() and dest.stat().st_size > 0 and not force:
            return dest
        dest.parent.mkdir(parents=True, exist_ok=True)
        tmp = dest.with_suffix(dest.suffix + ".part")
        if self.offline:
            raise OfflineError(f"オフラインモードのため取得できません: {url}")
        host = urlparse(url).netloc
        lim = self._limiter(host)
        lim.acquire()
        try:
            with self.client.stream("GET", url, timeout=self.host_timeout(host)) as r:
                if r.status_code == 404:
                    raise NotFoundError(url)
                r.raise_for_status()
                with open(tmp, "wb") as f:
                    for chunk in r.iter_bytes(1 << 20):
                        f.write(chunk)
        finally:
            lim.release()
        tmp.replace(dest)
        return dest

    def close(self) -> None:
        if self._client is not None:
            self._client.close()
            self._client = None
