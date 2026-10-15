import sqlite3
import zipfile

import pytest

from ludiglot.core.release_sources import rebuild_from_release_sources


def write_sources(path, sources):
    with zipfile.ZipFile(path,'w') as archive:
        for name,text in sources.items():
            archive.writestr('editor/build_support/TextSource/'+name,text)
    return path


def test_latest_explicit_source_and_canonical_target_only(tmp_path):
    archive = write_sources(tmp_path/'release.zip', {
        '34_source/3.4_part01.txt':'@@ lang_text.db||Text||1\nEN: Old sentence.\nVI: Do not use this target\n',
        '36_source/3.6_part01.txt':'@@ lang_text.db||Text||1\nEN: Latest sentence.\nSecond line.\n\n',
        '36_translations/3.6_part01.txt':'@@ lang_text.db||Text||1\nEN: Wrong sentence.\n',
    })
    target = rebuild_from_release_sources(archive,tmp_path/'derived.db',
                                          {('lang_text.db','1'):'Bản Việt tên Anh'},lambda:None)
    with sqlite3.connect(target) as connection:
        assert connection.execute('SELECT en,vi FROM entries').fetchall() == [('Latest sentence.\nSecond line.','Bản Việt tên Anh')]


@pytest.mark.parametrize('text',[
    '@@ lang_text.db||Text||1\nEN: First\n@@ lang_text.db||Other||1\nEN: Second',
    '@@ lang_text.db||Text||1\nEN: First\nEN: Duplicate',
    '@@ lang_text.db||Text||1\nVI: Missing English',
    '@@ ../lang_text.db||Text||1\nEN: Unsafe identity',
])
def test_ambiguous_or_missing_source_never_becomes_translation(tmp_path,text):
    archive = write_sources(tmp_path/'release.zip',{'36_source/3.6_part01.txt':text})
    with pytest.raises(ValueError,match='Không có ID'):
        rebuild_from_release_sources(archive,tmp_path/'derived.db',{('lang_text.db','1'):'Bản Việt'},lambda:None)
    assert not (tmp_path/'derived.db').exists()


def test_cancellation_does_not_create_database(tmp_path):
    archive = write_sources(tmp_path/'release.zip',{'36_source/3.6_part01.txt':'@@ lang_text.db||Text||1\nEN: Hi'})
    def cancel():
        raise RuntimeError('cancelled')
    with pytest.raises(RuntimeError,match='cancelled'):
        rebuild_from_release_sources(archive,tmp_path/'derived.db',{},cancel)
    assert not (tmp_path/'derived.db').exists()
