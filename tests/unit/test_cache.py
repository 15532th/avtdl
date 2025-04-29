from pathlib import Path
from typing import List, Optional

import pytest

from avtdl.core.cache import FileCache, find_file, find_free_suffix, find_with_suffix, strip_rename_suffix

SUFFIX_TEMPLATE = FileCache.RENAME_SUFFIX


class TestStripRenameSuffix:
    testcases = {
        'no suffix': ('path/to/filename', 'path/to/filename'),
        'one suffix': ('path/to/filename', 'path/to/filename [2]'),
        'suffix is zero': ('path/to/filename', 'path/to/filename [0]'),
        'multiple suffixes': ('path/to [1]/longer [2] filename [3]', 'path/to [1]/longer [2] filename [3] [4]')
    }

    @pytest.mark.parametrize('path, path_with_suffix', testcases.values(), ids=testcases.keys())
    def test_strip_suffix(self, path, path_with_suffix):
        path = Path(path)
        path_with_suffix = Path(path_with_suffix)
        new_name = strip_rename_suffix(path_with_suffix.stem, SUFFIX_TEMPLATE)
        result = path_with_suffix.with_stem(new_name)
        assert result == path


def touch(path: Path, name: str):
    full_name = path / name
    with full_name.open('a'):
        pass
    return full_name


def prepare_files(workdir: Path, files: List[str]) -> List[Path]:
    """create empty files with given names in workdir"""
    return [touch(workdir, file) for file in files]


class TestFindFile:
    testcases = {
        # query, expected results, other files
        'no files': ('file1', [], []),
        'no matching files': ('file1', [], ['file2.jpg']),
        'one matching file': ('file1', ['file1.jpg'], ['file2.jpg', 'file3.jpg']),
        'two matching files': ('file1', ['file1.jpg', 'file1.exe'], ['file2.jpg', 'file3.jpg']),
    }

    @pytest.mark.parametrize('query, expected, unexpected', testcases.values(), ids=testcases.keys())
    def test_find_file(self, tmp_path, query: str, expected: List[str], unexpected: List[str]):
        _ = prepare_files(tmp_path, unexpected)
        expected_paths = prepare_files(tmp_path, expected)

        result = find_file(tmp_path / query)
        assert sorted(result) == sorted(expected_paths)


class TestFindFreeSuffix:
    testcases = {
        # query, expected filename, existing files
        'no files': ('file1', 'file1', []),
        'no matching files': ('file1', 'file1', ['file2.jpg']),
        'file exists': ('file1', 'file1 [1]', ['file1.jpg', 'file2.jpg']),
        'file with index exists': ('file1', 'file1 [2]', ['file1.jpg', 'file1 [1].jpg']),
        'gap in index': ('file1', 'file1 [2]', ['file1.jpg', 'file1 [1].jpg', 'file1 [3].jpg']),
        'no file, but file with index exists': ('file1', 'file1', ['file1 [0].jpg', 'file1 [1].jpg']),
    }

    @pytest.mark.parametrize('query, expected, files', testcases.values(), ids=testcases.keys())
    def test_find_free_suffix(self, tmp_path, query: str, expected: str, files: List[str]):
        prepare_files(tmp_path, files)

        result = find_free_suffix(tmp_path / query, SUFFIX_TEMPLATE)
        assert result.name == expected


class TestFindWithSuffix:
    testcases = {
        # query, expected filename, existing files
        'no files': ('file1', [], []),
        'no matching files': ('file1', [], ['file2.jpg']),
        'file exists': ('file1', ['file1.jpg'], ['file1.jpg', 'file2.jpg']),
        'two files exists': ('file1', ['file1.jpg', 'file1.exe'], ['file1.jpg', 'file1.exe', 'file2.png']),
        'file with index exists': ('file1', ['file1.jpg', 'file1 [1].jpg'], ['file1.jpg', 'file1 [1].jpg']),
        'no file, but file with index exists': ('file1',
                                                ['file1 [0].jpg', 'file1 [1].jpg'],
                                                ['file1 [0].jpg', 'file1 [1].jpg', 'file2.jpg', 'file2 [1].jpg']),
        'gap in index': ('file1',
                         ['file1.jpg', 'file1 [1].jpg', 'file1 [3].jpg'],
                         ['file1.jpg', 'file1 [1].jpg', 'file1 [3].jpg']),
    }

    @pytest.mark.parametrize('query, expected, files', testcases.values(), ids=testcases.keys())
    def test_find_free_suffix(self, tmp_path, query: str, expected: List[str], files: List[str]):
        prepare_files(tmp_path, files)

        results = find_with_suffix(tmp_path / query, SUFFIX_TEMPLATE)
        results_names = [result.name for result in results]
        assert sorted(results_names) == sorted(expected)


def file_cache(tmp_path):
    return FileCache(tmp_path, '.part')


class TestFileCacheFindFile:
    QUERY = 'file1'
    URL = 'http://example.com/file1.jpg'

    testcases = {
        # expected result filename, existing files
        'no files': (None, []),
        'no matching files': (None, ['file2.jpg']),
        'one matching file': ('file1.jpg', ['file1.jpg', 'file2.jpg', 'file3.jpg']),
        'same extension preferred for two matching files': ('file1.jpg',
                                                            ['file1.exe', 'file2.jpg', 'file3.jpg']),
        'higher index preferred for two matching files': ('file1 [3].jpg',
                                                          ['file1.jpg', 'file1 [1].jpg', 'file1 [3].jpg']),
    }

    @pytest.mark.parametrize('expected, files', testcases.values(), ids=testcases.keys())
    def test_find_file(self, tmp_path, expected: Optional[str], files: List[str]):
        prepare_files(tmp_path, files)
        expected = touch(tmp_path, expected) if expected else None

        result = file_cache(tmp_path)._find_file(tmp_path / self.QUERY, self.URL)
        assert result == expected
