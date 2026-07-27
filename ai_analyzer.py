#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
ABD9音乐文件筛查工具 - AI 分析模块
调用云端 LLM API（OpenAI 兼容格式）分析相似/近似文件组是否为同一首歌
支持分批发送，避免输出 token 超限
"""

import json
import re
import requests
from typing import Dict, List, Optional, Callable

from utils import format_duration

# 每批最大组数
BATCH_SIZE = 20




def _extract_title_key(name: str) -> Optional[str]:
    """
    从文件名中提取歌曲名关键词（去除歌手前缀）。
    格式: "歌手名 - 歌曲名.ext" 或 "歌手名-歌曲名.ext"
    返回歌曲名（小写），若无法解析则返回 None。
    含"伴奏"的文件会被标记为不同 key，与原版区分开。
    末尾括号内的版本说明会被去除，使同歌不同版本归为同一 key。
    """
    import re
    stem = name.rsplit('.', 1)[0] if '.' in name else name
    # 优先 " - " 分隔（最常用）
    if ' - ' in stem:
        parts = stem.split(' - ', 1)
        title = parts[1].strip()
        if title:
            title = _normalize_title(title, name)
            return title
    # 尝试 "-" 分隔（无空格）
    hyphen_idx = stem.rfind('-')
    if hyphen_idx > 0:
        artist = stem[:hyphen_idx].strip()
        title = stem[hyphen_idx + 1:].strip()
        if artist and title and len(artist) >= 2 and len(title) >= 2:
            title = _normalize_title(title, name)
            return title
    return None


def _normalize_title(title: str, full_name: str = '') -> str:
    """
    规范化歌曲名：去除末尾括号内容、去除末尾纯数字、转小写。
    含"伴奏"的追加 __acc 后缀，与原件区分。
    """
    title = title.strip().lower()
    # 去除末尾括号内容，如 (Explicit)、(片刻)、（伴奏）等
    title = re.sub(r'\s*[（(][^）)]*[）)]$', '', title).strip()
    # 去除末尾纯数字（如 "See You Again 1" → "See You Again"）
    title = re.sub(r'\s+\d+$', '', title).strip()
    # 含"伴奏"的标记为不同 key
    if '伴奏' in full_name:
        title += '__acc'
    return title


def _precluster_by_filename(group: list, file_tags: Dict[str, dict]) -> Optional[List[List[int]]]:
    """
    通过文件名解析对分组进行预聚类。
    
    规则：提取每个文件的歌曲名（去除歌手前缀），
    如果同组内所有文件都能提取出歌曲名，则按歌曲名聚类。
    歌曲名相同 → 同一首歌；不同 → 不同歌曲。
    
    Returns:
        [[idx1, idx2, ...], ...] 聚类结果，或 None（无法预聚类，交给 AI）
    """
    title_keys = {}  # title_key -> list of indices
    for idx, (path, info) in enumerate(group):
        # 优先用 ID3 标题标签（同样做规范化）
        tags = file_tags.get(path, {})
        title_from_tag = tags.get('title')
        if title_from_tag and title_from_tag.strip():
            key = title_from_tag.strip().lower()
            # 同样去除末尾括号内容
            key = re.sub(r'\s*[（(][^）)]*[）)]$', '', key).strip()
            # 含"伴奏"的标记为不同 key
            name = info.get('name', '')
            if '伴奏' in name:
                key += '__acc'
            title_keys.setdefault(key, []).append(idx)
            continue
        
        # 从文件名提取
        name = info.get('name', '')
        key = _extract_title_key(name)
        if key:
            title_keys.setdefault(key, []).append(idx)
        else:
            # 无法提取，交给 AI
            return None
    
    # 所有文件都提取成功，按 title_key 聚类
    clusters = list(title_keys.values())
    return clusters if clusters else None


def _build_batch_prompt(batch_groups: list, global_start: int,
                        file_tags: Dict[str, dict]) -> str:
    """
    构建一批 group 的 prompt 文本（使用全局索引）

    Args:
        batch_groups: 当前批的分组
        global_start: 当前批在全局中的起始索引
        file_tags: {path: {title, artist}}
    """
    lines = []
    for i, group in enumerate(batch_groups):
        real_idx = global_start + i
        lines.append(f"组 {real_idx}:")
        for path, info in group:
            tags = file_tags.get(path, {})
            name = info.get('name', '?')
            title = tags.get('title') or '?'
            artist = tags.get('artist') or '?'
            dur = format_duration(info.get('duration'))
            size = f"{info.get('size', 0) / 1024:.0f}KB"
            lines.append(f"  - {name} | 标题:{title} | 歌手:{artist} | 时长:{dur} | 大小:{size}")
        lines.append("")
    return "\n".join(lines)


def _call_batch_api(
    batch_groups: list,
    global_start: int,
    file_tags: Dict[str, dict],
    config: dict,
    batch_index: int,
    total_batches: int,
) -> List[dict]:
    """
    发送一批分组到 AI API 并解析返回

    Returns:
        [{"group_index": int, "same_song": bool, "reason": str}, ...]
    """
    endpoint = config.get('endpoint', '').rstrip('/')
    api_key = config.get('api_key', '')
    model = config.get('model', 'gpt-4o-mini')

    group_text = _build_batch_prompt(batch_groups, global_start, file_tags)

    system_prompt = (
        "你是一个音乐文件分析助手。你的任务是从以下三个维度综合判断每组文件"
        "是否为「同一首歌」的不同版本（如不同格式、不同码率、不同音量、"
        "伴奏版/原版、Live版等），还是「不同的歌曲」。\n\n"
        "### 三个分析维度（按权重排序）：\n\n"
        "1️⃣ **歌曲名（最高权重）**\n"
        "   - 标题标签(title)或文件名主干高度相似或相同 → 大概率同一首歌\n"
        "   - **关键规则**：文件名中「 - 」或「-」后面的部分才是真正的歌曲名。"
        "如果「 - 」后面的歌曲名明显不同，即使前面的歌手名相同也是不同歌曲\n"
        "   - 注意：括号内的版本说明（如(Live)、(Remix)、(伴奏)、(独唱版)）不影响「同一首歌」的判断\n\n"
        "2️⃣ **歌手（中权重）**\n"
        "   - 歌手标签相同 **且** 歌曲名也相同 → 同一首歌\n"
        "   - **重要**：歌手标签相同 **但** 歌曲名明显不同 → 不同歌曲（这是最常见的误判！）\n"
        "   - 歌手标签不同（如不同歌手演唱）→ 即使歌曲名相同也是翻唱版本\n"
        "   - 如果歌手标签缺失（显示为 ?），则忽略此维度\n\n"
        "3️⃣ **时长（辅助参考）**\n"
        "   - 两文件时长差异在 30% 以内 → 不反对同一首歌\n"
        "   - 两文件时长差异超过 50% → 降低「同一首歌」的可能性\n"
        "   - 如果时长缺失，则忽略此维度\n\n"
        "### ⚠️ 最重要的一条规则（必须遵守）：\n"
        "**同组内，如果多个文件共享同一个歌手名，但「 - 」后面的歌曲名明显不同，"
        "则它们是不同的歌曲，必须分别聚类！**\n"
        "   ✅ 正确示例：「林俊杰 - 杀手」vs「林俊杰 - 西界」→ 不同歌曲，聚类为 [[0],[1]]\n"
        "   ✅ 正确示例：「薛之谦 - 丑八怪」vs「薛之谦 - 怪咖」→ 不同歌曲，聚类为 [[0],[1]]\n"
        "   ✅ 正确示例：「张韶涵 - 呐喊」vs「张韶涵 - 天边」→ 不同歌曲，聚类为 [[0],[1]]\n"
        "   ✅ 正确示例：「陈奕迅 - 完」vs「陈奕迅 - 阿牛」→ 不同歌曲，聚类为 [[0],[1]]\n"
        "   ✅ 正确示例：「李克勤 - 后悔」vs「李克勤 - 飞花」→ 不同歌曲，聚类为 [[0],[1]]\n"
        "   ✅ 正确示例：「谭咏麟 - 情人」vs「谭咏麟 - 还我真情」→ 不同歌曲，聚类为 [[0],[1]]\n"
        "   ✅ 正确示例：「许志安 - 烂泥」vs「许志安 - 爱你」→ 不同歌曲，聚类为 [[0],[1]]\n"
        "   ✅ 正确示例：「张碧晨 - 梦底」vs「张碧晨 - 笼」→ 不同歌曲，聚类为 [[0],[1]]\n"
        "   ✅ 正确示例：「邓寓君 - 关山酒」vs「邓寓君 - 凉夜横塘」→ 不同歌曲，聚类为 [[0],[1]]\n"
        "   ✅ 正确示例：「白小白 - 我爱你不问归期」vs「白小白 - 爱是」→ 不同歌曲，聚类为 [[0],[1]]\n"
        "   ❌ 错误示例：把「林俊杰 - 杀手」和「林俊杰 - 西界」聚类在一起 [[0,1]]\n\n"
        "### 同一首歌的判断标准：\n"
        "   以下情况是同一首歌的不同版本，应放在同一个 cluster 中：\n"
        "   ✅ 歌手和歌曲名完全相同，仅文件格式不同（如 .mp3 vs .m4a）\n"
        "   ✅ 歌曲名相同，仅大小写不同（如 STAY vs Stay）\n"
        "   ✅ 歌曲名相同，一个带版本说明（如 (Explicit)、1、2、Live、独唱版）\n"
        "   ✅ 歌曲名相同，一个带括号备注（如「Moment(片刻)」vs「Moment(去见你)」）\n\n"
        "### 输出要求：\n"
        "请严格按 JSON 格式返回，不要包含其它文字。"
    )

    user_prompt = (
        f"以下共有 {len(batch_groups)} 组文件。请分析每组内哪些文件属于同一首歌。\n\n"
        f"{group_text}\n"
        "请返回 JSON（不要包含其它文字）：\n"
        '{"results": [{"group_index":0,"clusters":[[0,1],[2]],"reason":"文件0和1是同一首歌不同格式；文件2是另一首"}, ...]}\n'
        "说明：clusters 中的每个子数组代表一组「同一首歌」的文件索引（基于该组内的文件顺序，从0开始）。"
        "同一首歌的文件放在同一个子数组中，不同歌曲分开。单首歌曲的数组只有一个元素。"
    )

    # 动态计算 max_tokens：每组约 100 tokens 输出 + 300 余量
    max_tokens = len(batch_groups) * 100 + 300

    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt}
        ],
        "temperature": 0.1,
        "response_format": {"type": "json_object"},
        "max_tokens": max_tokens
    }

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json"
    }

    api_url = f"{endpoint}/chat/completions"

    try:
        resp = requests.post(api_url, headers=headers, json=payload, timeout=120)
        resp.raise_for_status()
        data = resp.json()
    except requests.exceptions.Timeout:
        raise ConnectionError(
            f"第 {batch_index + 1}/{total_batches} 批请求超时（120秒），"
            "请检查网络或改用更快的模型"
        )
    except requests.exceptions.RequestException as e:
        raise ConnectionError(f"第 {batch_index + 1}/{total_batches} 批请求失败: {e}")

    try:
        content = data['choices'][0]['message']['content']
        result = json.loads(content)
        judgments = result.get('results', [])
    except (KeyError, IndexError, json.JSONDecodeError) as e:
        raise ValueError(
            f"第 {batch_index + 1}/{total_batches} 批返回格式异常: {e}"
        )

    return judgments


def analyze_groups(
    groups: list,
    file_tags: Dict[str, dict],
    config: dict,
    progress_callback: Optional[Callable[[int, int, str], None]] = None
) -> List[dict]:
    """
    调用 AI API 分析每组文件是否为同一首歌（分批发送）

    Args:
        groups: 相似/近似分组列表，每组为 [(path, info), ...]
        file_tags: {path: {title, artist}} 标签字典
        config: {endpoint, api_key, model}
        progress_callback: (current, total, message) -> None

    Returns:
        [{"group_index": int, "same_song": bool, "reason": str}, ...]

    Raises:
        ConnectionError: API 请求失败
        ValueError: 返回格式异常
    """
    if not config.get('endpoint') or not config.get('api_key'):
        raise ValueError("API 配置不完整，请在「API 配置」中填写")

    total = len(groups)
    if total == 0:
        return []

    all_judgments = []

    # Step 1: 尝试对每组进行预聚类（通过文件名解析），无需 AI
    ai_groups = []       # 需要 AI 分析的组（原 group_index）
    ai_group_indices = []  # 对应的原始索引
    for gi, group in enumerate(groups):
        pre_clusters = _precluster_by_filename(group, file_tags)
        if pre_clusters is not None:
            all_judgments.append({
                "group_index": gi,
                "clusters": pre_clusters,
                "reason": "按歌曲名预聚类"
            })
        else:
            ai_groups.append(group)
            ai_group_indices.append(gi)

    # Step 2: 剩余分组交给 AI 分析
    ai_batches = 0
    if ai_groups:
        total_batches = (len(ai_groups) + BATCH_SIZE - 1) // BATCH_SIZE
        ai_batches = total_batches
        for batch_idx in range(total_batches):
            start = batch_idx * BATCH_SIZE
            end = min(start + BATCH_SIZE, len(ai_groups))
            batch = ai_groups[start:end]
            batch_orig_indices = ai_group_indices[start:end]

            if progress_callback:
                msg = f"正在分析第 {batch_idx + 1}/{total_batches} 批（AI 分析）..."
                progress_callback(batch_idx, total_batches, msg)

            # AI 接收到的组索引从 0 开始，需传 global_start 映射回原始索引
            try:
                batch_results = _call_batch_api(
                    batch, batch_orig_indices[0], file_tags, config,
                    batch_idx, total_batches
                )
                # 修正 group_index 为原始索引
                for r in batch_results:
                    raw_gi = r.get('group_index')
                    if raw_gi is not None:
                        # 计算在原始 groups 中的索引
                        offset = raw_gi - batch_orig_indices[0]
                        if 0 <= offset < len(batch_orig_indices):
                            r['group_index'] = batch_orig_indices[offset]
                all_judgments.extend(batch_results)
            except (ConnectionError, ValueError) as e:
                all_judgments.append({
                    "group_index": -1,
                    "same_song": False,
                    "reason": f"AI 分析失败: {e}"
                })

    if progress_callback:
        progress_callback(ai_batches, ai_batches, f"分析完成，共处理 {total} 组")

    return all_judgments
