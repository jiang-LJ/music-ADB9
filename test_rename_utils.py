# -*- coding: utf-8 -*-
"""rename_utils 模块单元测试"""

import os
import tempfile

from rename_utils import build_new_filename, should_rename, is_copy_suffix, detect_manual_review


class TestIsCopySuffix:
    """数字后缀副本识别"""

    def test_copy_suffix(self):
        assert is_copy_suffix('王菲 - 红豆(1).mp3') is True
        assert is_copy_suffix('王菲 - 红豆(12).mp3') is True
        assert is_copy_suffix('王菲 - 红豆(123).mp3') is True

    def test_not_copy_suffix(self):
        assert is_copy_suffix('王菲 - 红豆.mp3') is False
        assert is_copy_suffix('王菲 - 红豆(2022).mp3') is False   # 4位年份
        assert is_copy_suffix('王菲 - 红豆(1).flac') is True
        assert is_copy_suffix('红豆(1)') is True


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


class TestAdvancedCleanup:
    """重命名后残留问题的进阶清理规则"""

    def test_artist_trailing_ampersand(self):
        assert build_new_filename('ALen & 陈小春 & - 街角的晚风(奥语版).mp3') == \
            'ALen & 陈小春 - 街角的晚风(奥语版).mp3'

    def test_artist_amp_dash_between(self):
        """MissGoog & - 薛之谦 → MissGoog & 薛之谦（& 连接，- 多余）"""
        assert build_new_filename('MissGoog & - 薛之谦 - 雪落下的声音.mp3') == \
            'MissGoog & 薛之谦 - 雪落下的声音.mp3'

    def test_artist_trailing_dash(self):
        assert build_new_filename('静怡DJ-RINO- - Sleeptalking(韩国版).m4a') == \
            '静怡DJ-RINO - Sleeptalking(韩国版).m4a'

    def test_underscore_artist_kept(self):
        """cici_ 等以下划线结尾的合法歌手名不应被删下划线"""
        assert build_new_filename('cici_ - 越来越不懂.m4a') == 'cici_ - 越来越不懂.m4a'
        assert build_new_filename('相依为命_ - _R.mp3') == '相依为命_ - _R.mp3'

    def test_semicolon_artists(self):
        assert build_new_filename('A1 TRIP;Nick.Y;云推荐 - butterfly.mp3') == \
            'A1 TRIP & Nick.Y & 云推荐 - butterfly.mp3'

    def test_artist_duplicate_merged(self):
        assert build_new_filename('Carly Rae Jepsen - Carly Rae Jepsen - Call Me Maybe.mp3') == \
            'Carly Rae Jepsen - Call Me Maybe.mp3'
        assert build_new_filename('Beyond(黄家驹) - Beyond(黄家驹) - 灰色轨迹.mp3') == \
            'Beyond(黄家驹) - 灰色轨迹.mp3'

    def test_artist_duplicate_kept_when_same_as_title(self):
        """black black heart - black black heart（歌名本身=歌手名）不应合并"""
        assert build_new_filename('black black heart - black black heart.mp3') == \
            'black black heart - black black heart.mp3'

    def test_title_contains_artist(self):
        assert build_new_filename('尚士达 - 尚士达-生而为人.m4a') == '尚士达 - 生而为人.m4a'

    def test_version_trailing_singer_removed(self):
        assert build_new_filename('QQ音乐 - 我在人民广场吃炸鸡(Live)-彭滢.mp3') == \
            'QQ音乐 - 我在人民广场吃炸鸡(Live).mp3'
        assert build_new_filename('电音任瑶 - 02-格啦啦(DJ版)-电音任瑶+徐友根+爱仔仔.m4a') == \
            '电音任瑶 - 格啦啦(DJ版).m4a'

    def test_sequencenum_cleaned(self):
        assert build_new_filename('边江 - 18.月色真美(Live)-边江.mp3') == \
            '边江 - 月色真美(Live).mp3'
        assert build_new_filename('EA7 - 05.EA7 - 潮汐旋律(DJ原声).m4a') == \
            'EA7 - 潮汐旋律(DJ原声).m4a'

    def test_subtitle_hyphen_kept(self):
        """(300c) - Tempo di Menuetto 这类副标题不应被当作版本后歌手删除"""
        assert build_new_filename('Hilary Hahn - Sonata in E minor K. 304 (300c) - Tempo di Menuetto.mp3') == \
            'Hilary Hahn - Sonata in E minor K. 304 (300c) - Tempo di Menuetto.mp3'

    def test_empty_parens_removed(self):
        assert build_new_filename('Musicsum - Musicsum - Hello () .mp3') == \
            'Musicsum - Hello.mp3'

    def test_zero_width_chars_cleaned(self):
        """零宽/不可见字符清理（U+3164、零宽空格等）"""
        assert build_new_filename('\u3164\u3164 - 世事难两全.mp3') == '世事难两全.mp3'
        assert build_new_filename('张韶涵\u3164 & \u3164\u3164 - 欧若拉.mp3') == '张韶涵 - 欧若拉.mp3'
        assert build_new_filename('LINGLING7 & 心病 & 网友锐明\u3164\u3164\u3164 & \u200b\u200b\u2060 - Stressed Out.mp3') == \
            'LINGLING7 & 心病 & 网友锐明 - Stressed Out.mp3'

    def test_trailing_amp_underscore_cleaned(self):
        """歌手列表尾部 & _ 占位清理"""
        assert build_new_filename('Sol3曜槿 & 鸾仟羽 & 桥上 & _ - 黑糖秀主题曲 (feat. 黑涩会美眉).mp3') == \
            'Sol3曜槿 & 鸾仟羽 & 桥上 - 黑糖秀主题曲 (feat. 黑涩会美眉).mp3'

    def test_title_inner_mp3_removed(self):
        assert build_new_filename('墨染辞 - 斩春秋戏腔.mp3.mp3') == '墨染辞 - 斩春秋戏腔.mp3'

    def test_html_entity_decoded(self):
        assert build_new_filename("Girl's Day - &#44592;&#45824;&#54644;.mp3") == "Girl's Day - 기대해.mp3"

    def test_title_trailing_underscore_removed(self):
        assert build_new_filename('Justin Bieber - What Do You Mean_.mp3') == \
            'Justin Bieber - What Do You Mean.mp3'
        assert build_new_filename('Black Eyed Peas - Where Is The Love_.mp3') == \
            'Black Eyed Peas - Where Is The Love.mp3'

    def test_artist_duplicate_segment_merged(self):
        """歌手段完全重复去重（& 内重复段）"""
        assert build_new_filename('李荣浩 & 李荣浩 - 李白;李白.mp3') == '李荣浩 - 李白;李白.mp3'
        assert build_new_filename('小表哥 & 小表哥 - 土耳其舞蹈.mp3') == '小表哥 - 土耳其舞蹈.mp3'
        assert build_new_filename('VaSka & VaSka & 品味 - DJ Antoine-Arabian Adventure(CaSsie VaSka 品味 remix).mp3') == \
            'VaSka & 品味 - DJ Antoine-Arabian Adventure(CaSsie VaSka 品味 remix).mp3'
        assert build_new_filename('AhmaTjan Sahra & Dilyar official & Dilyar official - Plain Jene.mp3') == \
            'AhmaTjan Sahra & Dilyar official - Plain Jene.mp3'
        assert build_new_filename('Vince Giordano & The Nighthawks & Vince Giordano & Nighthawks Orchestra - Manhattan.mp3') == \
            'Vince Giordano & The Nighthawks & Nighthawks Orchestra - Manhattan.mp3'

    def test_artist_duplicate_ww_kept(self):
        """W&W 是真实组合名，重复段必须保留"""
        assert build_new_filename('W & W & AXMO & Sonja - Rave Love.mp3') == \
            'W & W & AXMO & Sonja - Rave Love.mp3'
        assert build_new_filename('W & W & Kizuna AI - The Light.mp3') == \
            'W & W & Kizuna AI - The Light.mp3'
        assert build_new_filename('W & W & Nicky Romero - Ups & Downs.mp3') == \
            'W & W & Nicky Romero - Ups & Downs.mp3'

    def test_amp_dash_with_multi_artist(self):
        """MissGoog & - 薛之谦 → & 是连接符；张韶涵ㅤ & ㅤㅤ - 欧若拉 → & 是多余末尾符"""
        assert build_new_filename('MissGoog & - 薛之谦 - 雪落.mp3') == 'MissGoog & 薛之谦 - 雪落.mp3'
        assert build_new_filename('ALen & 陈小春 & - 街角的晚风(奥语版).mp3') == \
            'ALen & 陈小春 - 街角的晚风(奥语版).mp3'
        assert build_new_filename('张韶涵\u3164 & \u3164\u3164 - 欧若拉.mp3') == '张韶涵 - 欧若拉.mp3'


class TestManualReview:
    """detect_manual_review：无法自动修复的异常文件名检测"""

    def test_gbk_mojibake_detected(self):
        assert detect_manual_review('ôá¶É - °®¶ûÀ¼»Ã¼ by ôá¶É.mp3') != ''
        assert detect_manual_review('Ôç°² - ½­ÄÏ.mp3') != ''
        assert detect_manual_review('Ð¤°î - »ÃÏë¼´ÐËÇú.mp3') != ''

    def test_download_residue_detected(self):
        assert detect_manual_review('Various Artists - 2someone_starunkind_lanfranchifarinaradio_itp881000027.m4a') != ''
        assert detect_manual_review('中国音乐公告牌 - 李嘉格_谢谢你爱我_181109_CD.m4a') != ''

    def test_normal_names_not_detected(self):
        assert detect_manual_review('Lemâitre - Closer.mp3') == ''
        assert detect_manual_review('NO BATIDÃO - Teste.mp3') == ''
        assert detect_manual_review('cici_ - 越来越不懂.m4a') == ''
        assert detect_manual_review('闻人听书_ - 虞兮叹.m4a') == ''
        assert detect_manual_review('Joji - 13_Afterthought.m4a') == ''
        assert detect_manual_review('王菲 - 红豆(1).mp3') == ''
        assert detect_manual_review('李荣浩 - 李白;李白.mp3') == ''
        assert detect_manual_review('伍佰 & China Blue - 夏夜晚风.mp3') == ''
        assert detect_manual_review('Dvorák - 新世界交响曲.mp3') == ''
        assert detect_manual_review('São Paulo - Sampa.mp3') == ''
        assert detect_manual_review('Stevie Wonder - Part-Time Lover.mp3') == ''


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
