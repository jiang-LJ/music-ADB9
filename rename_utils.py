# -*- coding: utf-8 -*-

"""
ABD9音乐文件筛查器 - 文件重命名工具模块
按照「重命名方案.md」的规则判定文件名是否需要重命名，并生成新文件名。

规则（按顺序执行）：
1. 多歌手分隔符统一为 " & "（, 、 _ + 多空格）
2. 版本括号统一为半角 ()，未闭合自动补全
3. 版本词统一（Live/DJ版/Remix 等映射表）
4. 繁体转简体（opencc，fallback 内置映射表）
5. 空格规范（全角空格、多空格 → 单个半角空格）
6. 歌名内 "-"（副标题）保留
7. 非法字符防护（Windows 非法字符、首尾空白、尾部句点）
"""

import re
from typing import Optional, Tuple

# ─────────────────────────── 常量 ───────────────────────────

# 版本词映射表（括号内容小写后查表）
_VERSION_MAP = {
    'live': 'Live', 'live版': 'Live', 'live演唱版': 'Live', 'live现场版': 'Live',
    'dj版': 'DJ版', 'dj': 'DJ版', 'dj车载版': 'DJ版', 'dj 车载版': 'DJ版',
    'dj完整版': 'DJ版', 'dj氛围版': 'DJ版', 'dj现场版': 'DJ版',
    'remix': 'Remix', 'remix版': 'Remix',
    'phonk': 'PHONK', 'phonk.pt1': 'PHONK', 'phonk版': 'PHONK',
    'slowed': 'Slowed', 'super slowed': 'Slowed',
    'explicit': 'Explicit',
    'acoustic': 'Acoustic',
    'instrumental': 'Instrumental',
    'original mix': 'Original Mix',
    'radio edit': 'Radio Edit',
    'album version': 'Album Version',
    'demo': 'Demo',
    'sped up': 'Sped Up',
}

# Windows 文件名非法字符（不含路径分隔符 / \）
_ILLEGAL_CHARS = set(':*?"<>|')

# 内置繁→简 fallback 映射（opencc 不可用时的兜底，覆盖常用字）
_TC_TO_SC_FALLBACK = str.maketrans({
    '來': '来', '裏': '里', '裡': '里', '妳': '你', '爲': '为', '與': '与',
    '無': '无', '們': '们', '個': '个', '這': '这', '後': '后', '發': '发',
    '於': '于', '愛': '爱', '說': '说', '時': '时', '間': '间', '會': '会',
    '還': '还', '學': '学', '電': '电', '頭': '头', '點': '点', '幾': '几',
    '氣': '气', '從': '从', '對': '对', '讓': '让', '夢': '梦', '離': '离',
    '變': '变', '聽': '听', '親': '亲', '舊': '旧', '東': '东', '樂': '乐',
    '難': '难', '萬': '万', '風': '风', '雲': '云', '靜': '静', '緣': '缘',
    '處': '处', '勝': '胜', '經': '经', '濟': '济', '總': '总', '質': '质',
    '實': '实', '單': '单', '雙': '双', '龍': '龙', '飛': '飞', '馬': '马',
    '鳥': '鸟', '魚': '鱼', '麗': '丽', '僅': '仅', '盡': '尽', '進': '进',
    '遠': '远', '運': '运', '遊': '游', '陽': '阳', '陰': '阴', '陳': '陈',
    '黃': '黄', '張': '张', '趙': '赵', '孫': '孙', '劉': '刘', '吳': '吴',
    '鄭': '郑', '許': '许', '謝': '谢', '葉': '叶', '蘇': '苏', '鄧': '邓',
    '馮': '冯', '蔣': '蒋', '蔡': '蔡', '龔': '龚', '顧': '顾', '羅': '罗',
    '錢': '钱', '楊': '杨', '鄒': '邹', '蔣': '蒋', '陸': '陆', '鍾': '钟',
})


# ─────────────────────────── opencc 单例 ───────────────────────────

_OPENCC = None
_OPENCC_AVAILABLE = None


def _get_opencc():
    """获取 opencc 转换器（t2s），失败返回 None"""
    global _OPENCC, _OPENCC_AVAILABLE
    if _OPENCC_AVAILABLE is not None:
        return _OPENCC
    try:
        from opencc import OpenCC
        _OPENCC = OpenCC('t2s')
        _OPENCC_AVAILABLE = True
    except Exception:
        _OPENCC = None
        _OPENCC_AVAILABLE = False
    return _OPENCC


def to_simplified(text: str) -> str:
    """繁体转简体（opencc 优先，fallback 内置映射表）"""
    cc = _get_opencc()
    if cc is not None:
        try:
            return cc.convert(text)
        except Exception:
            pass
    return text.translate(_TC_TO_SC_FALLBACK)


# ─────────────────────────── 拆分 ───────────────────────────

def split_artist_title(stem: str) -> Tuple[Optional[str], str]:
    """
    按第一个 ' - ' 分隔符拆分文件名主干。
    返回 (artist, title)；找不到分隔符时返回 (None, stem)。
    匹配要求 '-' 两侧至少一个空白，避免误拆 "AK-47"、"1022-比尔的歌" 等。
    """
    m = re.search(r'\s+-\s+', stem)
    if not m:
        return None, stem
    artist = stem[:m.start()].strip()
    title = stem[m.end():].strip()
    return artist, title


# ─────────────────────────── 各步骤转换 ───────────────────────────

def _normalize_separator_spaces(text: str) -> str:
    """全角空格 → 半角，多个连续空格 → 单个半角空格"""
    text = text.replace('\u3000', ' ')
    return re.sub(r'\s+', ' ', text).strip()


def _unify_multi_artists(artist: str) -> str:
    """多歌手分隔符统一为 ' & '（两侧带空格）"""
    if not artist:
        return artist
    # 多空格（≥2）连接的歌手 → " & "（先处理，避免被压缩）
    artist = re.sub(r'\s{2,}', ' & ', artist)
    # 各种分隔符 → " & "
    artist = re.sub(r'[、，]', ' & ', artist)
    artist = re.sub(r',', ' & ', artist)
    # 下划线：两侧有内容才转（避免 "cici_" 这类歌手名被破坏）
    artist = re.sub(r'\s+_\s+', ' & ', artist)          # "A _ B"
    artist = re.sub(r'(?<=\S)_(?=\S)', ' & ', artist)   # "A_B"
    # 加号：两侧有内容才转
    artist = re.sub(r'\s+\+\s+', ' & ', artist)         # "A + B"
    artist = re.sub(r'(?<=\S)\+(?=\S)', ' & ', artist)  # "A+B"
    # 无空格 & → " & "
    artist = re.sub(r'(?<=\S)&(?=\S)', ' & ', artist)
    # 压缩多余空格
    artist = _normalize_separator_spaces(artist)
    return artist


def _unify_parens(stem: str) -> str:
    """全角括号 → 半角；括号未闭合自动补 ')'"""
    s = stem.replace('（', '(').replace('）', ')')
    if s.count('(') > s.count(')'):
        s += ')' * (s.count('(') - s.count(')'))
    return s


def _title_case_english(text: str) -> str:
    """英文词首字母大写（仅纯 ASCII 词）"""
    def _cap(m):
        return m.group(0)[0].upper() + m.group(0)[1:]
    return re.sub(r'\b[a-z][a-z0-9]*', _cap, text)


def _unify_version_parens(stem: str) -> str:
    """统一括号内版本词（映射表 + 英文首字母大写 + 中文保留）"""
    def _replace(m):
        content = m.group(1).strip()
        if not content:
            return '()'
        key = content.lower().strip()
        if key in _VERSION_MAP:
            return f"({_VERSION_MAP[key]})"
        # 尝试末尾版本词匹配（如 "完整版Dj版" 结尾 "dj版" → "完整版DJ版"）
        # 仅匹配带「版」字的词，避免误拆 "Filatov & Karas Remix" 等歌名内英文词
        for k in sorted(_VERSION_MAP, key=len, reverse=True):
            if '版' in k and key.endswith(k):
                prefix = content[:len(content) - len(k)].strip()
                return f"({prefix}{_VERSION_MAP[k]})"
        # 纯英文 → 首字母大写；含中文/数字 → 保留原样（仅去多余空格）
        if re.fullmatch(r'[A-Za-z0-9 &.\'-]+', content):
            return f"({_title_case_english(_normalize_separator_spaces(content))})"
        return f"({_normalize_separator_spaces(content)})"
    return re.sub(r'\(([^()]*)\)', _replace, stem)


def _clean_illegal_chars(stem: str) -> str:
    """去除 Windows 非法字符、首尾空白、尾部句点"""
    s = ''.join(ch for ch in stem if ch not in _ILLEGAL_CHARS)
    s = s.strip().rstrip('.')
    return s


# ─────────────────────────── 主入口 ───────────────────────────

def build_new_filename(filename: str) -> str:
    """
    根据命名规则生成新文件名。
    返回与原名不同的新名；若无需重命名或转换后为空，返回原名。
    """
    # 拆分扩展名
    dot = filename.rfind('.')
    if dot <= 0:
        stem, ext = filename, ''
    else:
        stem, ext = filename[:dot], filename[dot:]

    # 1. 拆分 歌手 - 歌名（正则容忍多空格分隔符）
    artist, title = split_artist_title(stem)

    if artist is not None:
        # 2. 多歌手分隔符统一（多空格 → &）
        artist = _unify_multi_artists(artist)
        title = _normalize_separator_spaces(title)
        # 3. 繁体转简体（歌手 + 歌名）
        artist = to_simplified(artist)
        title = to_simplified(title)
        stem = f"{artist} - {title}"
    else:
        stem = _normalize_separator_spaces(stem)
        stem = to_simplified(stem)

    # 4. 括号统一 + 未闭合补全
    stem = _unify_parens(stem)
    # 5. 版本词统一
    stem = _unify_version_parens(stem)
    # 6. 空格最终规范
    stem = _normalize_separator_spaces(stem)
    # 7. 非法字符清理
    stem = _clean_illegal_chars(stem)

    if not stem:
        return filename
    new_name = stem + ext
    return new_name


def should_rename(filename: str) -> bool:
    """判定文件名是否需要重命名"""
    return build_new_filename(filename) != filename
