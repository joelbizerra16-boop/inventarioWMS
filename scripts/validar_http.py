import re
import urllib.error
import urllib.parse
import urllib.request
from http.cookiejar import CookieJar

BASE = 'http://127.0.0.1:8000'


def main():
    cj = CookieJar()
    opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(cj))

    login_html = opener.open(f'{BASE}/accounts/login/').read().decode()
    csrf = re.search(r'name="csrfmiddlewaretoken" value="([^"]+)"', login_html).group(1)
    data = urllib.parse.urlencode({
        'username': 'admin.demo',
        'password': 'Demo@2026!',
        'csrfmiddlewaretoken': csrf,
    }).encode()
    req = urllib.request.Request(
        f'{BASE}/accounts/login/',
        data=data,
        headers={'Referer': f'{BASE}/accounts/login/'},
    )
    try:
        opener.open(req)
    except urllib.error.HTTPError:
        pass

    for path in (
        '/',
        '/produtos/',
        '/posicoes/',
        '/estoque-sap/',
        '/inventarios/',
        '/confronto/',
        '/aprovacao/',
        '/consolidacao/',
        '/ciclico/',
    ):
        response = opener.open(f'{BASE}{path}')
        print(f'autenticado {path} -> {response.status}')

    class NoRedirectHandler(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, req, fp, code, msg, headers, newurl):
            return None

    no_redirect = urllib.request.build_opener(NoRedirectHandler())
    try:
        no_redirect.open(f'{BASE}/')
        print('sem login / -> 200 (FALHA: deveria redirecionar)')
    except urllib.error.HTTPError as exc:
        print(f'sem login / -> {exc.code}')
    except urllib.error.URLError:
        print('sem login / -> 302 (bloqueio OK)')


if __name__ == '__main__':
    main()
