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
import html
from typing import Optional, Tuple

# ─────────────────────────── 常量 ───────────────────────────

# 版本词映射表（括号内容小写后查表）
_VERSION_MAP = {    'live': 'Live', 'live版': 'Live', 'live演唱版': 'Live', 'live现场版': 'Live',
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

# 真实组合名白名单：歌手段重复出现时保留（W&W 是荷兰 DJ 组合，非笔误重复）
_KEEP_DUPLICATE_SEGMENTS = {'w', 'w&w'}

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
    """多歌手分隔符统一为 ' & '（两侧带空格），并清理歌手名尾部残留"""
    if not artist:
        return artist
    # 多空格（≥2）连接的歌手 → " & "（先处理，避免被压缩）
    artist = re.sub(r'\s{2,}', ' & ', artist)
    # 各种分隔符 → " & "
    artist = re.sub(r'[、，]', ' & ', artist)
    artist = re.sub(r',', ' & ', artist)
    artist = re.sub(r'\s*;\s*', ' & ', artist)          # 分号 "A;B" → "A & B"
    # 下划线：两侧有内容才转（避免 "cici_" 这类歌手名被破坏）
    artist = re.sub(r'\s+_\s+', ' & ', artist)          # "A _ B"
    artist = re.sub(r'(?<=\S)_(?=\S)', ' & ', artist)   # "A_B"
    # 加号：两侧有内容才转
    artist = re.sub(r'\s+\+\s+', ' & ', artist)         # "A + B"
    artist = re.sub(r'(?<=\S)\+(?=\S)', ' & ', artist)  # "A+B"
    # 无空格 & → " & "
    artist = re.sub(r'(?<=\S)&(?=\S)', ' & ', artist)
    # 歌手间 "- &" 模式（如 "薛之谦- & MissGoog" → "薛之谦 & MissGoog"）
    artist = re.sub(r'\s*-\s*&\s*', ' & ', artist)
    # 压缩多余空格
    artist = _normalize_separator_spaces(artist)
    # 歌手段完全重复去重（李荣浩 & 李荣浩 → 李荣浩；W&W 等真实组合名白名单保护）
    parts = [p.strip() for p in artist.split('&')]
    seen = set()
    kept = []
    for p in parts:
        key = p.strip().lower()
        if key in seen and key not in _KEEP_DUPLICATE_SEGMENTS:
            continue
        seen.add(key)
        # ① 纯中文歌手名尾部句点清理（张信哲. → 张信哲；eel./CR3./尘ah. 含字母数字不动）
        if re.fullmatch(r'[\u4e00-\u9fff]+[.．。]', p.strip()):
            p = p.strip()[:-1]
        kept.append(p)
    artist = ' & '.join([p for p in kept if p])
    # 清理歌手名尾部残留分隔符（多余 " & "、"-"；不删下划线——cici_ 等是合法歌手名）
    artist = re.sub(r'(?:\s*&\s*)+$', '', artist)
    # 尾部独立 "_" 占位歌手（Sol3... & _ → 删 " & _"；cici_ 这类不删）
    artist = re.sub(r'\s*&\s*_+\s*$', '', artist)
    artist = re.sub(r'-\s*$', '', artist)
    artist = artist.strip()
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


def _wrap_bare_version_suffix(title: str) -> str:
    """
    歌名末尾无括号的版本词（live/dj/remix/slowed 等）包进括号并统一。
    误判防护：
    - 版本词前是英文字母（Alive/Premix）→ 不包
    - 版本词前是空格，且空格前整段为纯英文、无分隔符（如 "Because You Live" 是歌名）→ 不包
    """
    low = title.lower()
    for k in sorted(_VERSION_MAP, key=len, reverse=True):
        m = re.search(r'(?<=[^a-z])' + re.escape(k) + r'$', low)
        if not m:
            continue
        start = m.start()
        if start == 0:
            continue
        prev = title[start - 1]
        # 版本词前紧邻英文字母（alive/premix 等单词的一部分）
        if prev.isascii() and prev.isalpha():
            continue
        # 版本词前是空格：空格前整段纯英文且无分隔符 → 歌名的一部分，不包
        if prev.isspace():
            before = title[:start].rstrip()
            if before and '-' not in before and all(c.isascii() for c in before):
                continue
        prefix = title[:start].rstrip(' -_').strip()
        if prefix:
            return f"{prefix}({_VERSION_MAP[k]})"
    return title


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

    # 0. 清理零宽/不可见字符（U+3164 韩文填充、零宽空格/连字符、不可见数学运算符、BOM、软连字符等）
    stem = re.sub(r'[\u200b-\u200f\u202a-\u202e\u2060-\u2064\ufeff\u00ad\u3164\u180e\u061c]', '', stem)
    # 清理后若歌手为空（如 "ㅤㅤ - 歌名" → " - 歌名"），去掉开头的分隔符
    stem = re.sub(r'^\s*-\s*', '', stem)
    # 0.5 HTML 实体解码（&#44592;&#45824;&#54644; → 기대해）
    stem = html.unescape(stem)

    # 修复 "& -" 相邻：前面已有 & → "&" 是多余末尾符、"-" 是主分隔符；
    # 前面无 & 但 "-" 后还有歌手-歌名（如 MissGoog & - 薛之谦 - 雪落）→ "&" 是连接符、"-" 多余；
    # 前面无 & 且 "-" 后直接是歌名（如 张韶涵ㅤ & ㅤㅤ - 欧若拉）→ "&" 是多余末尾符
    def _fix_amp_dash(m):
        prefix = m.string[:m.start()]
        rest = m.string[m.end():]
        if '&' in prefix:
            return ' - '
        if ' - ' in rest:
            return ' & '
        return ' - '
    stem = re.sub(r'\s*&\s*-\s*', _fix_amp_dash, stem)

    # 1. 拆分 歌手 - 歌名（正则容忍多空格分隔符）
    artist, title = split_artist_title(stem)

    if artist is not None:
        # 2. 多歌手分隔符统一（多空格 → &）+ 歌手名尾部清理
        artist = _unify_multi_artists(artist)
        title = _normalize_separator_spaces(title)
        # 2.0 歌名内嵌 .mp3 + 尾部下划线
        title = re.sub(r'\.mp3\b', '', title, flags=re.IGNORECASE)
        title = re.sub(r'_+$', '', title)
        # ③ 歌名开头单个下划线删除（_R → R；____ You 多下划线不动）
        title = re.sub(r'^_(?!_)', '', title)
        # ④ 中文歌名中间 _ → 空格（你好_再见 → 你好 再见；英文/其他语言不动）
        title = re.sub(r'(?<=[\u4e00-\u9fff])_(?=[\u4e00-\u9fff])', ' ', title)
        # ② 副本后缀 (1)-(9) 移除（张学友 - 情人 (Live)(1) → 情人 (Live)；DJ (23) 两位数字不动）
        title = re.sub(r'\s*\(([1-9])\)\s*$', '', title)
        # 2.1 序号前缀（18. / 02- 等开头数字序号）
        title = re.sub(r'^\s*\d{1,3}\s*[.、\-]\s*', '', title)
        # 2.2 歌手名重复：歌名以「歌手 - 」开头（Carly Rae Jepsen - Carly Rae Jepsen - Call Me Maybe）
        prefix = f"{artist} - "
        if title.lower().startswith(prefix.lower()):
            title = title[len(prefix):].strip()
        # 2.3 歌名含歌手名：歌名以「歌手名-」开头（尚士达-生而为人 → 生而为人）
        last_artist = artist.split(' & ')[-1].strip()
        if last_artist and title.lower().startswith(last_artist.lower() + '-'):
            title = title[len(last_artist):].lstrip('- ').strip()
        # 2.4 版本括号后跟 -歌手（仅限版本词括号，如 (Live)-彭滢 → (Live)；
        #     不动 (300c) - Tempo di Menuetto 这类副标题）
        title = re.sub(
            r'(\((?:live|dj|remix|伴奏|现场|女声|粤语|国语|独唱|合唱|钢琴|剧场)[^()]*\))\s*-\s*[^()]+$',
            r'\1', title, flags=re.IGNORECASE)
        # 3. 繁体转简体（歌手 + 歌名）
        artist = to_simplified(artist)
        title = to_simplified(title)
        stem = f"{artist} - {title}"
    else:
        stem = _normalize_separator_spaces(stem)
        stem = to_simplified(stem)

    # 4. 括号统一 + 未闭合补全
    stem = _unify_parens(stem)
    # 清理空括号（如 "...OST) ()" → "...OST)"）
    stem = re.sub(r'\s*\(\)', '', stem)
    # 4.5 无括号版本词包覆（歌名尾部 live/dj/remix 等 → (Live)/(DJ版)/(Remix)）
    a, t = split_artist_title(stem)
    if a is not None:
        t2 = _wrap_bare_version_suffix(t)
        if t2 != t:
            stem = f"{a} - {t2}"
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


def is_copy_suffix(name: str) -> bool:
    """
    判断文件名是否为数字后缀副本（如 红豆(1).mp3、我的歌(2).m4a）。
    括号内为 1-3 位纯数字（排除 4 位年份如 (2022)）。
    """
    base = name.rsplit('.', 1)[0] if '.' in name else name
    return re.search(r'\(\d{1,3}\)\s*$', base) is not None


def should_rename(filename: str) -> bool:
    """判定文件名是否需要重命名"""
    return build_new_filename(filename) != filename


# 连续 ≥4 个 latin-1 扩展字符（ôá¶É、°®¶ûÀ¼»Ã¼ 等 GBK 乱码特征；Lemâitre 仅 1 个、NO BATIDÃO 连续 2 个不误报）
_LATIN_EXT_RUN = re.compile(r'[\u0080-\u00ff]{4,}')


def detect_manual_review(filename: str) -> str:
    """
    检测无法自动可靠修复的异常文件名，返回异常原因；正常返回空字符串。
    命中的文件应在重命名时弹窗让用户手动输入新名。
    """
    name = filename.rsplit('/', 1)[-1].rsplit('\\', 1)[-1]
    stem = name.rsplit('.', 1)[0] if '.' in name else name

    # 1. 乱码字符检测：替换字符 / 控制字符 / 连续拉丁扩展乱码串
    if '\ufffd' in stem:
        return '文件名含乱码字符（�）'
    if re.search(r'[\x00-\x1f\x7f-\x9f]', stem):
        return '文件名含控制字符'
    if _LATIN_EXT_RUN.search(stem):
        return '文件名疑似 GBK 乱码（含连续拉丁扩展字符）'

    # 2. 下载残留命名检测：歌名部分是三段以上下划线小写串（2someone_starunkind_xxx）/ 含日期/ID 串
    if ' - ' in stem:
        _title = stem.split(' - ', 1)[1].strip()
    else:
        _title = stem
    if re.fullmatch(r'[a-z0-9]+(?:_[a-z0-9]+){2,}', _title, re.IGNORECASE):
        return '下载残留命名（下划线串）'
    if re.search(r'_\d{5,}_', _title) or re.fullmatch(r'[^_]+_\d{4}_[a-z0-9]{0,4}', _title, re.IGNORECASE):
        return '下载残留命名（含日期/ID 串）'

    return ''
