import http.cookiejar as cookiejar
import http.cookies

import pytest

from avtdl.core.cookies import AnotherAiohttpCookieJar, AnotherCurlCffiCookieJar


@pytest.fixture(params=[AnotherAiohttpCookieJar, AnotherCurlCffiCookieJar])
def jar_class(request):
    '''Return the concrete class based on the parametrization name'''
    return request.param


def make_morsel(name: str, value: str) -> http.cookies.Morsel:
    morsel: http.cookies.Morsel = http.cookies.Morsel()
    morsel.set(name, value, value)
    return morsel


@pytest.fixture
def empty_jar(jar_class):
    return jar_class()


def sample_cookie(name: str, value: str, domain: str = 'example.com', path: str = '/') -> cookiejar.Cookie:
    return cookiejar.Cookie(
        version=0,
        name=name,
        value=value,
        port=None,
        port_specified=False,
        domain=domain,
        domain_specified=True,
        domain_initial_dot=False,
        path=path,
        path_specified=True,
        secure=False,
        expires=None,
        discard=True,
        comment=None,
        comment_url=None,
        rest={},
        rfc2109=False,
    )


@pytest.fixture
def populated_cookiejar():
    cj = cookiejar.CookieJar()
    cj.set_cookie(sample_cookie(name='c1', value='v1', domain='a.com'))
    cj.set_cookie(sample_cookie(name='c2', value='v2', domain='b.org'))
    return cj


def test_set_and_get_basic(empty_jar):
    empty_jar.set('session', 'abc123', 'https://example.com')
    assert empty_jar.get('session') == 'abc123'

    # Overwrite existing key
    empty_jar.set('session', 'def456', 'https://example.com')
    assert empty_jar.get('session') == 'def456'


def test_get_missing_key_returns_none(empty_jar):
    assert empty_jar.get('nonexistent') is None


@pytest.mark.parametrize(
    'mapping',
    [
        {'a': '1', 'b': '2'},
        {'x': make_morsel('x', 'foo'), 'y': make_morsel('y', 'bar')},
        {'mix': 'plain', 'morsel': make_morsel('morsel', 'zoo')},
    ],
)
def test_update_cookies(empty_jar, mapping):
    empty_jar.update_cookies(mapping)

    for key, val in mapping.items():
        if isinstance(val, str):
            expected = val
        else:
            expected = val.value
        assert empty_jar.get(key) == expected


def test_to_cookie_jar(empty_jar):
    empty_jar.set('alpha', 'A', 'https://a.com')
    empty_jar.set('beta', 'B', 'https://b.com')

    cj = empty_jar.to_cookie_jar()

    names = {c.name for c in cj}
    assert names == {'alpha', 'beta'}


def test_from_cookie_jar(jar_class):
    raw = cookiejar.CookieJar()
    raw.set_cookie(sample_cookie(name='alpha', value='A', domain='https://a.com'))
    raw.set_cookie(sample_cookie(name='beta', value='B', domain='https://b.com'))

    jar = jar_class.from_cookie_jar(raw)

    assert jar.get('alpha') == 'A'
    assert jar.get('beta') == 'B'


def test_mutating_extracted_cookiejar_reflects_on_original(jar_class):
    original = jar_class()
    original.set('original', 'original_value', 'example.com')

    extracted_jar = original._cookies
    copy = jar_class(extracted_jar)
    copy.set('copy', 'copy_value', 'example.com')

    assert original.get('copy') == 'copy_value'
    assert copy.get('original') == 'original_value'
