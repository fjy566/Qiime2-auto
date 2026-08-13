"""Allow-listed QIIME 2 classifier resources and downloads."""

from __future__ import annotations

import os
from pathlib import Path
from urllib.parse import urlparse
from urllib.request import Request, urlopen


PROJECT_ROOT = Path(__file__).resolve().parents[1]
CLASSIFIER_ROOT = PROJECT_ROOT / "classifiers"
ALLOWED_HOSTS = {"data.qiime2.org"}

# These URLs are published by QIIME 2.  Keep the catalog in code so the browser
# cannot turn the local service into an arbitrary URL downloader.
OFFICIAL_CLASSIFIERS = (
    {
        "id": "silva-138-99-full-length",
        "name": "SILVA 138 · 99% 全长 16S",
        "filename": "silva-138-99-nb-classifier.qza",
        "url": "https://data.qiime2.org/classifiers/sklearn-1.4.2/silva/silva-138-99-nb-classifier.qza",
        "kind": "full-length",
        "recommended": True,
        "description": "官方预训练的全长 SILVA 分类器。若你的 reads 已截成某个区域，通常应使用对应区域分类器或自行训练。",
    },
    {
        "id": "greengenes-13-8-515-806",
        "name": "Greengenes 13_8 · 515F/806R",
        "filename": "gg-13-8-99-515-806-nb-classifier.qza",
        "url": "https://data.qiime2.org/classifiers/sklearn-1.4.2/greengenes/gg-13-8-99-515-806-nb-classifier.qza",
        "kind": "region",
        "recommended": False,
        "description": "官方 515F/806R 区域分类器，适合已经明确使用该区域的 16S 数据。",
    },
)


def _entry(classifier_id: str) -> dict:
    for item in OFFICIAL_CLASSIFIERS:
        if item["id"] == classifier_id:
            return dict(item)
    raise ValueError("未知的官方分类器")


def classifier_catalog(root: str | Path = CLASSIFIER_ROOT) -> list[dict]:
    directory = Path(root)
    catalog: list[dict] = []
    for source in OFFICIAL_CLASSIFIERS:
        item = dict(source)
        target = directory / source["filename"]
        item.update({"path": str(target.resolve()), "downloaded": target.is_file(), "size": target.stat().st_size if target.is_file() else 0})
        catalog.append(item)
    return catalog


def classifier_path(classifier_id: str, root: str | Path = CLASSIFIER_ROOT) -> Path:
    source = _entry(classifier_id)
    return Path(root) / source["filename"]


def download_classifier(classifier_id: str, root: str | Path = CLASSIFIER_ROOT, progress=None) -> str:
    source = _entry(classifier_id)
    parsed = urlparse(source["url"])
    if parsed.scheme != "https" or parsed.hostname not in ALLOWED_HOSTS:
        raise ValueError("分类器下载地址不是受信任的 QIIME 2 官方地址")
    directory = Path(root)
    directory.mkdir(parents=True, exist_ok=True)
    target = directory / source["filename"]
    if target.is_file() and target.stat().st_size > 0:
        if progress:
            progress(target.stat().st_size, target.stat().st_size)
        return str(target.resolve())

    partial = target.with_name(f".{target.name}.part")
    request = Request(source["url"], headers={"User-Agent": "QIIME2-Auto/1.0"})
    downloaded = 0
    try:
        with urlopen(request, timeout=90) as response, partial.open("wb") as handle:
            total = int(response.headers.get("Content-Length") or 0)
            while True:
                chunk = response.read(1024 * 1024)
                if not chunk:
                    break
                handle.write(chunk)
                downloaded += len(chunk)
                if progress:
                    progress(downloaded, total)
        if total and downloaded != total:
            raise OSError(f"分类器下载不完整：收到 {downloaded} / {total} 字节")
        os.replace(partial, target)
    except Exception:
        try:
            partial.unlink(missing_ok=True)
        except OSError:
            pass
        raise
    return str(target.resolve())

