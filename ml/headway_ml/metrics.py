"""Error metrics and the cluster bootstrap."""

import numpy as np
import numpy.typing as npt

FloatArray = npt.NDArray[np.float64]


def summarize(err: FloatArray) -> dict[str, float]:
    """MAE, RMSE and the shares within 60 and 120 s of signed errors (forecast minus label)."""
    a = np.abs(err)
    return {
        "n": float(len(err)),
        "mae": float(a.mean()),
        "rmse": float(np.sqrt((err**2).mean())),
        "within_60": float((a <= 60).mean() * 100),
        "within_120": float((a <= 120).mean() * 100),
    }


def bootstrap_mae_ci(
    err: FloatArray, clusters: npt.NDArray[np.int64], reps: int, seed: int
) -> tuple[float, float]:
    """95% interval of the MAE, resampling whole clusters (examples of one prediction time share
    their situation and are not independent)."""
    a = np.abs(err)
    n_clusters = int(clusters.max()) + 1
    sums = np.bincount(clusters, weights=a, minlength=n_clusters)
    counts = np.bincount(clusters, minlength=n_clusters).astype(np.float64)
    rng = np.random.default_rng(seed)
    idx = rng.integers(0, n_clusters, size=(reps, n_clusters))
    maes = sums[idx].sum(axis=1) / counts[idx].sum(axis=1)
    lo, hi = np.percentile(maes, [2.5, 97.5])
    return float(lo), float(hi)


def paired_bootstrap_mae_diff(
    abs_a: FloatArray, abs_b: FloatArray, clusters: npt.NDArray[np.int64], reps: int, seed: int
) -> tuple[float, float]:
    """95% interval of MAE(a) - MAE(b) on the same rows, resampling the same clusters for both
    (a negative difference means a is better). The rows of a and b are the same examples, in the
    same order, so each replicate compares the two methods on one resampled set of prediction
    times."""
    n_clusters = int(clusters.max()) + 1
    sa = np.bincount(clusters, weights=abs_a, minlength=n_clusters)
    sb = np.bincount(clusters, weights=abs_b, minlength=n_clusters)
    counts = np.bincount(clusters, minlength=n_clusters).astype(np.float64)
    rng = np.random.default_rng(seed)
    idx = rng.integers(0, n_clusters, size=(reps, n_clusters))
    n = counts[idx].sum(axis=1)
    diff = sa[idx].sum(axis=1) / n - sb[idx].sum(axis=1) / n
    lo, hi = np.percentile(diff, [2.5, 97.5])
    return float(lo), float(hi)
