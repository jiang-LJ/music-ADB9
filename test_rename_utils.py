# -*- coding: utf-8 -*-
"""rename_utils 模块单元测试"""

import os
import tempfile

from rename_utils import build_new_filename, should_rename


class TestBuildNewFilename:
    """命名规则转换测试"""

    def test_multi_artist_comma(self):
        assert build_new_filename('李克勤, 容祖儿 - 世界真细小.mp3') == \
            '李克勤 & 容祖儿 - 世界真细小.mp3'

    def test_multi_artist_dunhao(self):
        assert build_new_filename('Ayo97、周思涵 - 感谢你曾来过.mp3') == \
            'Ayo97 & 周思涵 - 感谢你曾来过.mp3'

    def test_multi_artist_multi_spaces(self):
        assert build_new_filename('海鸣威  吴琼 - 老人与海.mp3') == \
            '海鸣威 & 吴琼 - 老人与海.mp3'

    def test_multi_artist_underscore_with_spaces(self):
        assert build_new_filename('海鸣威 _ 吴琼 - 老人与海.mp3') == \
            '海鸣威 & 吴琼 - 老人与海.mp3'

    def test_ampersand_no_spaces(self):
        assert build_new_filename('Alec Benjamin&Alessia Cara - Let Me Down Slowly.m4a') == \
            'Alec Benjamin & Alessia Cara - Let Me Down Slowly.m4a'

    def test_separator_multi_spaces(self):
        assert build_new_filename('George Winston  -  Variations On The Canon By Pachelbel.mp3') == \
            'George Winston - Variations On The Canon By Pachelbel.mp3'

    def test_traditional_to_simplified(self):
        assert build_new_filename('辛曉琪 - 女人何苦為難女人.m4a') == \
            '辛晓琪 - 女人何苦为难女人.m4a'

    def test_fullwidth_parens(self):
        assert build_new_filename('田园 - 后来的后来（DJ西米版）.mp3') == \
            '田园 - 后来的后来(DJ西米版).mp3'

    def test_version_word_unify(self):
        assert build_new_filename('张信哲 - 信仰 (完整版Dj版).mp3') == \
            '张信哲 - 信仰 (完整版DJ版).mp3'

    def test_live_version_unify(self):
        assert build_new_filename('Beyond - 喜欢你(Live版).mp3') == \
            'Beyond - 喜欢你(Live).mp3'

    def test_unclosed_paren(self):
        assert build_new_filename('Adele - Someone Like You (Live Acousti.mp3') == \
            'Adele - Someone Like You (Live Acousti).mp3'

    def test_remix_in_title_kept(self):
        """歌名内 'Filatov & Karas Remix' 不应被破坏"""
        assert build_new_filename('Antoine Chambe - Andalusia(Filatov & Karas Remix).m4a') == \
            'Antoine Chambe - Andalusia(Filatov & Karas Remix).m4a'

    def test_artist_underscore_suffix_kept(self):
        """歌手名末尾下划线（cici_）不应被当作多歌手分隔符"""
        assert build_new_filename('cici_ - 越来越不懂.m4a') == 'cici_ - 越来越不懂.m4a'

    def test_title_hyphen_kept(self):
        """歌名内 '-'（副标题）保留"""
        assert build_new_filename('Georges Delerue - A LITTLE ROMANCE - Main Title.mp3') == \
            'Georges Delerue - A LITTLE ROMANCE - Main Title.mp3'

    def test_artist_hyphen_kept(self):
        """歌手名内 'AK-47' 保留"""
        assert build_new_filename('Banda AK-47 - 我是奶龙（星光闪闪）.mp3') == \
            'Banda AK-47 - 我是奶龙(星光闪闪).mp3'

    def test_standard_name_unchanged(self):
        """标准格式不动"""
        assert build_new_filename('伍佰 & China Blue - 夏夜晚风.mp3') == \
            '伍佰 & China Blue - 夏夜晚风.mp3'

    def test_no_bracket_dj_wrapped(self):
        """无括号版本词 dj → 包进括号统一为 (DJ版)"""
        assert build_new_filename('AEC - 不染dj.m4a') == 'AEC - 不染(DJ版).m4a'

    def test_live_word_in_title_kept(self):
        """英文歌名含 Live（Because You Live / Alive）不应被误包"""
        assert build_new_filename('Jesse McCartney - Because You Live.mp3') == \
            'Jesse McCartney - Because You Live.mp3'
        assert build_new_filename('Blue - Alive.mp3') == 'Blue - Alive.mp3'

    def test_bare_version_wrapped(self):
        """中文歌名尾部的无括号版本词应包覆"""
        assert build_new_filename('泰山有货机 - 沙威玛传奇 Remix.mp3') == \
            '泰山有货机 - 沙威玛传奇(Remix).mp3'
        assert build_new_filename('王绎龙 - 午夜DJ.mp3') == '王绎龙 - 午夜(DJ版).mp3'
        assert build_new_filename('许巍 - 许巍《我们》2022LIVE.m4a') == \
            '许巍 - 许巍《我们》2022(Live).m4a'

    def test_should_rename(self):
        assert should_rename('李克勤, 容祖儿 - 世界真细小.mp3') is True
        assert should_rename('伍佰 & China Blue - 夏夜晚风.mp3') is False


class TestRenameOnDisk:
    """真实文件重命名/恢复测试（临时目录）"""

    def test_rename_and_restore(self):
        import rename_utils
        with tempfile.TemporaryDirectory() as tmp:
            old_name = '李克勤, 容祖儿 - 世界真细小.mp3'
            new_name = rename_utils.build_new_filename(old_name)
            assert new_name != old_name

            old_path = os.path.join(tmp, old_name)
            new_path = os.path.join(tmp, new_name)
            with open(old_path, 'w', encoding='utf-8') as f:
                f.write('test')

            # 重命名
            os.rename(old_path, new_path)
            assert os.path.exists(new_path)
            assert not os.path.exists(old_path)

            # 恢复
            os.rename(new_path, old_path)
            assert os.path.exists(old_path)
            assert not os.path.exists(new_path)
