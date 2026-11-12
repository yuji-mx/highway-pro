"""統計手法（§4.14.5）。scipy と同じ実装を使える箇所は scipy を使う。"""

from __future__ import annotations

import math
from typing import Any

import numpy as np
from numpy.typing import NDArray
from scipy import stats as sps

FloatArray = NDArray[np.float64]


def clean(x: Any) -> FloatArray:
    a = np.asarray(x, dtype=float)
    return a[~np.isnan(a)]


def describe(x: Any) -> dict[str, Any]:
    a = clean(x)
    if len(a) == 0:
        return {
            "n": 0,
            "mean": None,
            "sd": None,
            "median": None,
            "q1": None,
            "q3": None,
            "p10": None,
            "p90": None,
            "min": None,
            "max": None,
        }
    q = np.percentile(a, [10, 25, 50, 75, 90])
    return {
        "n": len(a),
        "mean": float(a.mean()),
        "sd": float(a.std(ddof=1)) if len(a) > 1 else 0.0,
        "median": float(q[2]),
        "q1": float(q[1]),
        "q3": float(q[3]),
        "p10": float(q[0]),
        "p90": float(q[4]),
        "min": float(a.min()),
        "max": float(a.max()),
    }


def mann_whitney(a: Any, b: Any, exact_n_max: int = 20) -> tuple[float | None, str]:
    """両側 Mann–Whitney U。n ≤ exact_n_max かつ同順位なしは正確検定、それ以外は連続性補正付き正規近似。"""
    x, y = clean(a), clean(b)
    if len(x) == 0 or len(y) == 0:
        return None, "none"
    ties = len(np.unique(np.concatenate([x, y]))) < len(x) + len(y)
    if max(len(x), len(y)) <= exact_n_max and not ties:
        r = sps.mannwhitneyu(x, y, alternative="two-sided", method="exact")
        return float(r.pvalue), "mannwhitney_exact"
    if np.all(np.concatenate([x, y]) == x[0]):
        return 1.0, "mannwhitney_asymptotic"
    r = sps.mannwhitneyu(x, y, alternative="two-sided", method="asymptotic", use_continuity=True)
    return float(r.pvalue), "mannwhitney_asymptotic"


def cliffs_delta(a: Any, b: Any) -> float | None:
    """δ = [#(b_j > a_i) − #(b_j < a_i)] / (n_a n_b)（B が大きいほど正）。"""
    x, y = clean(a), clean(b)
    if len(x) == 0 or len(y) == 0:
        return None
    xs = np.sort(x)
    gt = np.searchsorted(xs, y, side="left").sum()  # a_i < b_j の数
    lt = (len(xs) - np.searchsorted(xs, y, side="right")).sum()  # a_i > b_j の数
    return float((gt - lt) / (len(x) * len(y)))


def delta_magnitude(d: float) -> str:
    a = abs(d)
    return "negligible" if a < 0.147 else "small" if a < 0.33 else "medium" if a < 0.474 else "large"


def cohens_d(a: Any, b: Any) -> float | None:
    x, y = clean(a), clean(b)
    if len(x) < 2 or len(y) < 2:
        return None
    sp = math.sqrt(((len(x) - 1) * x.var(ddof=1) + (len(y) - 1) * y.var(ddof=1)) / (len(x) + len(y) - 2))
    if sp == 0:
        return 0.0
    return float((y.mean() - x.mean()) / sp)


def _boot_medians(x: FloatArray, n_boot: int, rng: np.random.Generator, resample_max_n: int) -> FloatArray:
    """ブートストラップ標本の中央値を n_boot 個生成する。

    n ≤ resample_max_n は通常の復元抽出。それより大きい場合は、復元抽出標本の中央値の分布が
    「P(中央値 ≤ x_(j)) = P(Binom(n, j/n) ≥ m)」（x は昇順、m = 中央の順位）で与えられることを使い、
    一様乱数の逆変換で直接サンプリングする（同じ分布を O(n + B log n) で得る。偶数 n は上下 2 つの順序統計量を
    同じ乱数で求めて平均する）。
    """
    n = len(x)
    if n <= resample_max_n:
        return np.median(x[rng.integers(0, n, (n_boot, n))], axis=1)
    xs = np.sort(x)
    j = np.arange(1, n + 1)
    u = rng.random(n_boot)

    def order_stat(m: int) -> FloatArray:
        cdf = sps.binom.sf(m - 1, n, j / n)  # P(Binom ≥ m)
        idx = np.searchsorted(cdf, u, side="left")
        return xs[np.clip(idx, 0, n - 1)]

    if n % 2 == 1:
        return order_stat((n + 1) // 2)
    return (order_stat(n // 2) + order_stat(n // 2 + 1)) / 2.0


def bootstrap_median_diff(
    a: Any, b: Any, n_boot: int = 2000, seed: int = 42, alpha: float = 0.05, resample_max_n: int = 2000
) -> tuple[float | None, list[float] | None]:
    """中央値差（B − A）とパーセンタイル・ブートストラップ信頼区間（乱数シード固定）。"""
    x, y = clean(a), clean(b)
    if len(x) == 0 or len(y) == 0:
        return None, None
    rng = np.random.default_rng(seed)
    bx = _boot_medians(x, n_boot, rng, resample_max_n)
    by = _boot_medians(y, n_boot, rng, resample_max_n)
    d = by - bx
    lo, hi = np.percentile(d, [100 * alpha / 2, 100 * (1 - alpha / 2)])
    return float(np.median(y) - np.median(x)), [float(lo), float(hi)]


def wilson_ci(k: int, n: int, z: float = 1.959963984540054) -> list[float] | None:
    if n == 0:
        return None
    p = k / n
    den = 1 + z * z / n
    c = (p + z * z / (2 * n)) / den
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / den
    return [max(0.0, c - h), min(1.0, c + h)]


def two_proportion_test(ka: int, na: int, kb: int, nb: int) -> tuple[float | None, str]:
    """2 標本比率の検定（カイ二乗、連続性補正なし）。期待度数 < 5 は Fisher の正確検定。"""
    if na == 0 or nb == 0:
        return None, "none"
    table = np.array([[ka, na - ka], [kb, nb - kb]])
    if table.sum(axis=0).min() == 0:
        return 1.0, "none"
    expected = sps.contingency.expected_freq(table)
    if expected.min() < 5:
        return float(sps.fisher_exact(table).pvalue), "fisher_exact"
    return float(sps.chi2_contingency(table, correction=False).pvalue), "chi2_2x2"


def categorical_test(
    a_counts: dict[str, int], b_counts: dict[str, int], mc_iter: int = 10000, seed: int = 42
) -> tuple[float | None, str]:
    """カテゴリ構成の χ² 検定。期待度数 < 5 のセルが 20 % 超なら Fisher–Freeman–Halton（モンテカルロ）。"""
    cats = sorted(set(a_counts) | set(b_counts))
    table = np.array([[a_counts.get(c, 0) for c in cats], [b_counts.get(c, 0) for c in cats]])
    table = table[:, table.sum(axis=0) > 0]
    if table.shape[1] < 2 or table.sum(axis=1).min() == 0:
        return None, "none"
    expected = sps.contingency.expected_freq(table)
    if (expected < 5).mean() > 0.2:
        return fisher_freeman_halton_mc(table, mc_iter, seed), "fisher_freeman_halton_mc"
    return float(sps.chi2_contingency(table, correction=False).pvalue), "chi2"


def fisher_freeman_halton_mc(table: NDArray[np.int64], n_iter: int = 10000, seed: int = 42) -> float:
    """r×c 表の正確検定をモンテカルロで近似（表の確率を統計量とする、周辺度数固定の置換）。"""
    rows = np.repeat(np.arange(table.shape[0]), table.sum(axis=1))
    cols = np.concatenate([np.repeat(np.arange(table.shape[1]), table[i]) for i in range(table.shape[0])])

    def logp(t: NDArray[np.int64]) -> float:
        from scipy.special import gammaln

        return float(
            gammaln(t.sum(axis=1) + 1).sum()
            + gammaln(t.sum(axis=0) + 1).sum()
            - gammaln(t.sum() + 1)
            - gammaln(t + 1).sum()
        )

    obs = logp(table)
    rng = np.random.default_rng(seed)
    count = 0
    r, c = table.shape
    for _ in range(n_iter):
        perm = rng.permutation(cols)
        t = np.zeros((r, c), dtype=np.int64)
        np.add.at(t, (rows, perm), 1)
        if logp(t) <= obs + 1e-9:
            count += 1
    return (count + 1) / (n_iter + 1)


def poisson_rate_test(ka: int, ta: float, kb: int, tb: float) -> float | None:
    """ポアソン率の比の検定（条件付き二項検定）。"""
    if ta <= 0 or tb <= 0 or ka + kb == 0:
        return None
    return float(sps.binomtest(ka, ka + kb, ta / (ta + tb)).pvalue)


def bh_qvalues(pvals: list[float | None]) -> list[float | None]:
    """Benjamini–Hochberg：q_(i) = min_{k≥i}(m p_(k) / k)。"""
    idx = [i for i, p in enumerate(pvals) if p is not None]
    m = len(idx)
    out: list[float | None] = [None] * len(pvals)
    if m == 0:
        return out
    ps = np.array([pvals[i] for i in idx], dtype=float)
    order = np.argsort(ps)
    q = ps[order] * m / np.arange(1, m + 1)
    q = np.minimum.accumulate(q[::-1])[::-1]
    q = np.minimum(q, 1.0)
    for rank, oi in enumerate(order):
        out[idx[oi]] = float(q[rank])
    return out


def fd_bins(data: FloatArray, max_bins: int = 30) -> FloatArray:
    a = clean(data)
    if len(a) == 0:
        return np.array([0.0, 1.0])
    lo, hi = float(a.min()), float(a.max())
    if hi <= lo:
        return np.array([lo - 0.5, hi + 0.5])
    iqr = float(np.subtract(*np.percentile(a, [75, 25])))
    width = 2 * iqr / (len(a) ** (1 / 3)) if iqr > 0 else (hi - lo) / 10
    n = int(np.clip(math.ceil((hi - lo) / width), 1, max_bins)) if width > 0 else 10
    return np.linspace(lo, hi, n + 1)


def ecdf(x: Any) -> list[list[float]]:
    a = np.sort(clean(x))
    n = len(a)
    return [[float(v), (i + 1) / n] for i, v in enumerate(a)]
