#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
ABD9音乐文件筛查工具 - 扫描核心算法
重复检测、相似检测、近似检测
"""

from rapidfuzz import fuzz
from collections import defaultdict
from typing import Dict, List, Tuple, Optional, Callable


def find_duplicates(files_a: Dict[str, dict], files_b: Dict[str, dict]) -> List[List[Tuple[str, dict]]]:
    """
    基于MD5查找重复文件组。
    如果MD5未计算，则退回到 file_name + size 组合。

    返回：
        [[(path, info), (path, info), ...], ...]
        每个子列表代表一组重复文件（至少2个）
    """
    groups: Dict[str, List[Tuple[str, dict]]] = {}

    for path, info in {**files_a, **files_b}.items():
        md5 = info.get('md5')
        if md5:
            key = md5
        else:
            key = f"{info['name'].lower()}|{info['size']}"
        groups.setdefault(key, []).append((path, info))

    return [g for g in groups.values() if len(g) >= 2]


def _compute_similar_paths(all_files: Dict[str, dict], dup_paths: set) -> set:
    """计算相似文件路径集合（与 find_similar 逻辑一致）"""
    sim_paths: set = set()

    # 条件①：同名同后缀，但大小不同
    name_groups: Dict[Tuple[str, str], List[Tuple[str, dict]]] = defaultdict(list)
    for path, info in all_files.items():
        if path in dup_paths:
            continue
        stem, suffix = _parse_name(info['name'])
        name_groups[(stem, suffix)].append((path, info))

    for members in name_groups.values():
        if len(members) < 2:
            continue
        sizes = {info['size'] for _, info in members}
        if len(sizes) <= 1:
            continue
        for path, _ in members:
            sim_paths.add(path)

    # 条件②：同名不同后缀
    stem_groups: Dict[str, List[Tuple[str, dict]]] = defaultdict(list)
    for path, info in all_files.items():
        if path in dup_paths or path in sim_paths:
            continue
        stem, _ = _parse_name(info['name'])
        stem_groups[stem].append((path, info))

    for members in stem_groups.values():
        if len(members) < 2:
            continue
        suffixes = {_parse_name(info['name'])[1] for _, info in members}
        if len(suffixes) <= 1:
            continue
        for path, _ in members:
            sim_paths.add(path)

    return sim_paths


def _parse_name(filename: str) -> Tuple[str, str]:
    """解析文件名为 stem 和 suffix（不区分大小写）"""
    lower = filename.lower()
    dot = lower.rfind('.')
    if dot > 0:
        return lower[:dot], lower[dot:]
    return lower, ''


def _get_excluded_paths(all_files: Dict[str, dict]) -> set:
    """计算已被归为重复的文件路径集合"""
    dup_paths = set()
    groups: Dict[str, List[str]] = {}
    for path, info in all_files.items():
        md5 = info.get('md5')
        key = md5 if md5 else f"{info['name'].lower()}|{info['size']}"
        groups.setdefault(key, []).append(path)
    for paths in groups.values():
        if len(paths) >= 2:
            dup_paths.update(paths)
    return dup_paths


def find_similar(files_a: Dict[str, dict], files_b: Dict[str, dict],
                 threshold: float = 0.75,
                 progress_callback: Optional[Callable[[int, int], None]] = None,
                 *, excluded_paths: Optional[set] = None) -> List[List[Tuple[str, dict]]]:
    """
    基于严格的同名匹配查找相似文件组。

    检测条件：
      1. 文件名+后缀名相同（不区分大小写），但文件大小不同
      2. 文件名相同（不区分大小写），但后缀名不同

    排除已被 find_duplicates 捕获的文件。

    Args:
        excluded_paths: 外部传入的重复文件路径集合。
                        为 None 时内部自动计算（向后兼容）。

    返回：
        [[(path, info), (path, info), ...], ...]
        每个子列表代表一组相似文件
    """
    all_files = {**files_a, **files_b}
    dup_paths = _get_excluded_paths(all_files) if excluded_paths is None else excluded_paths

    similar_groups: List[List[Tuple[str, dict]]] = []
    processed = set()

    # 条件①：同名同后缀，但大小不同
    name_groups: Dict[Tuple[str, str], List[Tuple[str, dict]]] = defaultdict(list)
    for path, info in all_files.items():
        if path in dup_paths:
            continue
        stem, suffix = _parse_name(info['name'])
        name_groups[(stem, suffix)].append((path, info))

    for members in name_groups.values():
        if len(members) < 2:
            continue
        sizes = {info['size'] for _, info in members}
        if len(sizes) <= 1:
            continue
        similar_groups.append(members)
        for path, _ in members:
            processed.add(path)

    # 条件②：同名不同后缀
    stem_groups: Dict[str, List[Tuple[str, dict]]] = defaultdict(list)
    for path, info in all_files.items():
        if path in dup_paths or path in processed:
            continue
        stem, _ = _parse_name(info['name'])
        stem_groups[stem].append((path, info))

    for members in stem_groups.values():
        if len(members) < 2:
            continue
        suffixes = {_parse_name(info['name'])[1] for _, info in members}
        if len(suffixes) <= 1:
            continue
        similar_groups.append(members)
        for path, _ in members:
            processed.add(path)

    if progress_callback:
        progress_callback(1, 1)

    return similar_groups


def find_approximate(files_a: Dict[str, dict], files_b: Dict[str, dict],
                     threshold: float = 0.95,
                     duration_threshold: Optional[float] = None,
                     progress_callback: Optional[Callable[[int, int], None]] = None,
                     *, excluded_paths: Optional[set] = None,
                     similar_paths: Optional[set] = None) -> List[List[Tuple[str, dict]]]:
    """
    基于文件名模糊匹配查找近似文件组（不区分后缀名）。

    检测条件：
      通过文件名的相似度阈值判断，不区分大小写、不区分后缀名。
      若指定 duration_threshold，仅当两个文件都有有效时长且时长差异在阈值内时才匹配。
      排除已被 find_duplicates 和 find_similar 捕获的文件。

    Args:
        excluded_paths: 外部传入的重复文件路径集合（来自 find_duplicates 的结果）。
                        为 None 时内部自动计算（向后兼容）。
        similar_paths: 外部传入的相似文件路径集合（来自 find_similar 的结果）。
                       为 None 时内部自动计算（向后兼容）。

    返回：
        [[(path, info), (path, info), ...], ...]
        每个子列表代表一组近似文件
    """
    all_files = {**files_a, **files_b}

    # 确定重复排除路径
    if excluded_paths is not None:
        dup_paths = excluded_paths
    else:
        dup_paths = _get_excluded_paths(all_files)

    # 确定相似排除路径
    if similar_paths is not None:
        sim_paths = similar_paths
    else:
        # 向后兼容：内部重新计算（与 find_similar 逻辑一致）
        sim_paths = _compute_similar_paths(all_files, dup_paths)

    # 收集未被排除的文件
    items = []
    for path, info in all_files.items():
        if path in dup_paths or path in sim_paths:
            continue
        stem, _ = _parse_name(info['name'])
        items.append((path, info, stem))

    n = len(items)
    if n < 2:
        return []

    # 按首字符分组优化
    char_groups: Dict[str, List[Tuple[int, str, str, dict]]] = defaultdict(list)
    for idx, (path, info, stem) in enumerate(items):
        first_char = stem[0] if stem else ''
        char_groups[first_char].append((idx, stem, path, info))

    parent = list(range(n))

    def _find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def _union(x, y):
        rx, ry = _find(x), _find(y)
        if rx != ry:
            parent[rx] = ry

    total_groups = len(char_groups)
    processed_groups = 0

    for _, members in char_groups.items():
        m = len(members)
        for i in range(m):
            idx_i, stem_i, _, info_i = members[i]
            for j in range(i + 1, m):
                idx_j, stem_j, _, info_j = members[j]
                ratio = fuzz.ratio(stem_i, stem_j) / 100.0
                if ratio >= threshold:
                    # 时长过滤：仅当两个文件都有有效时长时才检查
                    if duration_threshold is not None:
                        dur_i = info_i.get('duration')
                        dur_j = info_j.get('duration')
                        if dur_i is not None and dur_j is not None and dur_i > 0 and dur_j > 0:
                            shorter, longer = sorted([dur_i, dur_j])
                            if shorter / longer < duration_threshold:
                                continue
                    _union(idx_i, idx_j)

        processed_groups += 1
        if progress_callback:
            progress_callback(processed_groups, total_groups)

    clusters: Dict[int, List[Tuple[str, dict]]] = {}
    for idx, (path, info, _) in enumerate(items):
        root = _find(idx)
        clusters.setdefault(root, []).append((path, info))

    return [g for g in clusters.values() if len(g) >= 2]
