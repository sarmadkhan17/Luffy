"""Fail-closed dashboard authentication; no credentials in URLs or logs."""
import hashlib
import hmac
import secrets
import time
from http.cookies import SimpleCookie
from urllib.parse import parse_qs

from fastapi import Request
from fastapi.responses import HTMLResponse, JSONResponse

COOKIE = 'luffy_session'
TTL = 12 * 3600
LOGIN = '''<!doctype html><html><meta name="viewport" content="width=device-width">
<title>Luffy owner login</title><body><h1>Luffy owner login</h1>
<p>Enter the dashboard password stored in DASH_TOKEN on the server.</p>
<form><input id="password" type="password" autocomplete="current-password" required aria-label="Dashboard password">
<button>Sign in</button></form><p id="status"></p><script>
document.querySelector('form').onsubmit=async e=>{e.preventDefault();
const p=document.getElementById('password');
try {const r=await fetch('/auth/login',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({password:p.value})});
p.value=''; if(r.ok) location.replace('/'); else document.getElementById('status').textContent='Sign-in refused';}
catch(_){p.value='';document.getElementById('status').textContent='Connection unavailable';}};
</script></body></html>'''


class DashboardAuth:
    def __init__(self, token):
        self.token = token if token and token.strip() and token != 'luffy' else None

    def valid_token(self, value):
        return bool(self.token and isinstance(value, str) and
                    hmac.compare_digest(self.token.encode(), value.encode()))

    def signature(self, value):
        return hmac.new(self.token.encode(), ('session:'+value).encode(), hashlib.sha256).hexdigest()

    def session(self):
        value = str(int(time.time()) + TTL) + '.' + secrets.token_hex(16)
        return value + '.' + self.signature(value)

    def authenticated(self, scope):
        if not self.token:
            return False
        headers = dict(scope.get('headers', []))
        if self.valid_token(headers.get(b'x-luffy-token', b'').decode('latin1')):
            return True
        # Compatibility for existing read-only API clients; browser login uses cookies.
        query = parse_qs(scope.get('query_string', b'').decode('latin1'))
        if scope.get('method') == 'GET' and self.valid_token(query.get('token', [None])[0]):
            return True
        try:
            cookies = SimpleCookie(); cookies.load(headers.get(b'cookie', b'').decode('latin1'))
            value, sig = cookies[COOKIE].value.rsplit('.', 1)
            expires = int(value.split('.')[0])
            return (int(time.time()) < expires <= int(time.time()) + TTL and
                    hmac.compare_digest(sig, self.signature(value)))
        except (KeyError, ValueError, TypeError):
            return False

    @staticmethod
    def same_origin(scope):
        headers = dict(scope.get('headers', []))
        origin = headers.get(b'origin')
        scheme = 'https' if scope.get('scheme') in ('https', 'wss') else 'http'
        expected = (scheme + '://' + headers.get(b'host', b'').decode('latin1')).encode()
        # Header-authenticated scripts do not use browser cookies/ambient authority.
        return origin == expected or (origin is None and b'x-luffy-token' in headers)

    def install(self, app):
        auth = self

        @app.post('/auth/login')
        async def login(request: Request):
            if not auth.same_origin(request.scope):
                return JSONResponse({'error': 'origin refused'}, status_code=403)
            body = bytearray()
            async for chunk in request.stream():
                body.extend(chunk)
                if len(body) > 4096:
                    return JSONResponse({'error': 'request too large'}, status_code=413)
            import json
            try:
                value = json.loads(body).get('password')
            except (ValueError, AttributeError):
                value = None
            if not auth.valid_token(value):
                return JSONResponse({'error': 'sign-in refused'}, status_code=401)
            response = JSONResponse({'status': 'ok'}, headers={'Cache-Control': 'no-store'})
            response.set_cookie(COOKIE, auth.session(), max_age=TTL, httponly=True,
                                secure=request.url.scheme == 'https', samesite='strict')
            return response

        @app.post('/auth/logout')
        async def logout():
            response = JSONResponse({'status': 'signed out'})
            response.delete_cookie(COOKIE)
            return response

        class Guard:
            def __init__(self, app):
                self.app = app

            async def __call__(self, scope, receive, send):
                if scope['type'] not in ('http', 'websocket'):
                    return await self.app(scope, receive, send)
                ws = scope['type'] == 'websocket'
                login = scope['path'] == '/auth/login' and not ws
                allowed = auth.authenticated(scope)
                unsafe = ws or scope.get('method') not in ('GET', 'HEAD', 'OPTIONS')
                if unsafe and not auth.same_origin(scope):
                    if ws:
                        return await send({'type': 'websocket.close', 'code': 4403})
                    return await JSONResponse({'error': 'origin refused'}, status_code=403)(scope, receive, send)
                if not allowed and not login:
                    if ws:
                        return await send({'type': 'websocket.close', 'code': 4401})
                    response = (HTMLResponse(LOGIN, status_code=401) if scope['path'] == '/' else
                                JSONResponse({'error': 'authentication required'}, status_code=401))
                    response.headers['Cache-Control'] = 'no-store'
                    response.headers['Referrer-Policy'] = 'no-referrer'
                    return await response(scope, receive, send)
                return await self.app(scope, receive, send)
        app.add_middleware(Guard)
