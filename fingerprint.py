#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
ABD9音乐文件筛查工具 - 音频指纹模块
使用 Chromaprint (fpcalc) 计算音频指纹，用于验证两文件是否为同一音频内容。
"""

import os
from typing import Optional, Dict, List

from utils import get_app_dir


def _get_fpcalc_path() -> Optional[str]:
    """获取 fpcalc.exe 路径（打包后为 exe 同级，开发环境为项目根目录）"""
    app_dir = get_app_dir()
    candidates = [
        app_dir / "fpcalc.exe",
        app_dir / "_internal" / "fpcalc.exe",
    ]
    for p in candidates:
        if p.exists():
            return str(p)
    return None


def compute_fingerprint(filepath: str) -> Optional[str]:
    """
    计算单个音频文件的 Chromaprint 指纹。

    Args:
        filepath: 音频文件路径

    Returns:
        指纹字符串（十六进制），失败返回 None
    """
    try:
        import acoustid
        fpcalc = _get_fpcalc_path()
        if not fpcalc:
            return None
        duration, fingerprint = acoustid.fingerprint_file(filepath, fpcalc)
        return fingerprint
    except Exception:
        return None


def verify_cluster(group: list, fpcalc_path: str) -> Optional[bool]:
    """
    验证同一 cluster 内的文件是否真的是同一音频内容。
    对 cluster 内所有文件两两比对指纹。

    Args:
        group: [(path, info), ...] 同一 cluster 的文件

    Returns:
        True  → 所有文件指纹一致（真的是同一首歌）
        False → 存在指纹不一致的文件（不是同一首歌）
        None  → 无法判断（指纹计算失败）
    """
    if len(group) < 2:
        return True

    fingerprints = {}
    for path, _ in group:
        fp = compute_fingerprint(path)
        if fp is None:
            return None  # 无法计算指纹，不判断
        fingerprints[path] = fp

    # 两两比较
    paths = list(fingerprints.keys())
    first_fp = fingerprints[paths[0]]
    for p in paths[1:]:
        if fingerprints[p] != first_fp:
            return False  # 存在不一致
    return True  # 全部一致


def batch_verify(
    clusters_map: Dict[int, List[str]],
    file_info_map: dict
) -> Dict[int, bool]:
    """
    批量验证多个 cluster 的指纹一致性。

    Args:
        clusters_map: {group_index: [path1, path2, ...]} AI 返回的聚类
        file_info_map: {path: info_dict} 文件信息

    Returns:
        {group_index: True/False/None} 每个 cluster 的验证结果
    """
    results = {}
    need_fpcalc = _get_fpcalc_path()
    if not need_fpcalc:
        return {}

    for gi, paths in clusters_map.items():
        if len(paths) < 2:
            continue
        group = [(p, file_info_map.get(p, {})) for p in paths]
        results[gi] = verify_cluster(group, need_fpcalc)

    return results
