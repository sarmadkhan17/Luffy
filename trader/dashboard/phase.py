"""Explicit Dashboard startup/authority contract; config.yaml is the sole mode source."""
from pathlib import Path

READ_ONLY_GUI = 'READ_ONLY_GUI'
LIVE = 'LIVE'


def mode(cfg):
    value = (cfg.get('dashboard') or {}).get('startup_mode')
    if value not in (READ_ONLY_GUI, LIVE):
        raise ValueError('dashboard.startup_mode must explicitly be READ_ONLY_GUI or LIVE')
    return value


def startup_check(root, cfg):
    try:
        selected = mode(cfg)
    except ValueError:
        return dict(allow=False, result='FAIL', reasons=['dashboard_mode_invalid'])
    if selected == READ_ONLY_GUI:
        return dict(allow=True, result='PASS', mode=selected, reasons=[],
                    authority='READ_ONLY; no control, trading or provider calls')
    from trader.observability.dashboard_readiness import check
    result = check(root, cfg)
    if result['allow']:
        from trader.observability.preflight import collect_facts, evaluate
        result = evaluate(collect_facts(Path(root)))
    return {**result, 'mode': selected}


class ReadOnlyBoundary:
    """Reject every write/provider route before handler or gateway execution."""
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        path = scope.get('path', '')
        if scope['type'] == 'websocket':
            await send({'type': 'websocket.close', 'code': 1008})
            return
        if scope['type'] == 'http':
            method = scope.get('method')
            if path.startswith('/graphql') and method in ('GET', 'POST'):
                # Queries read historical journal evidence; mutations/subscriptions never execute.
                import json
                from urllib.parse import parse_qs
                from graphql import parse, OperationDefinitionNode, OperationType
                body = bytearray()
                try:
                    if method == 'POST':
                        while True:
                            event = await receive()
                            if event['type'] != 'http.request':
                                raise ValueError('request_unavailable')
                            body.extend(event.get('body', b''))
                            if len(body) > 65536:
                                raise ValueError('request_too_large')
                            if not event.get('more_body'):
                                break
                        query = json.loads(body)['query']
                    else:
                        query = parse_qs(scope.get('query_string', b'').decode())['query'][0]
                    operations = [n for n in parse(query).definitions
                                  if isinstance(n, OperationDefinitionNode)]
                    if not operations or any(n.operation != OperationType.QUERY for n in operations):
                        raise ValueError('read_queries_only')
                except Exception:
                    from starlette.responses import JSONResponse
                    await JSONResponse({'error': 'dashboard_read_only'}, status_code=403)(scope, receive, send)
                    return
                delivered = False
                async def replay():
                    nonlocal delivered
                    if not delivered:
                        delivered = True
                        return {'type': 'http.request', 'body': bytes(body), 'more_body': False}
                    return await receive()
                await self.app(scope, replay if method == 'POST' else receive, send)
                return
            blocked = ((method not in ('GET', 'HEAD', 'OPTIONS')
                        and path not in ('/auth/login', '/auth/logout'))
                       or path.startswith('/graphql')
                       or (path.startswith('/api/') and path not in ('/api/tracker', '/api/investigations/latest', '/api/attention/latest', '/api/logs', '/api/doctrine', '/api/brain/last_autopsy', '/api/review_status')))
            if blocked:
                from starlette.responses import JSONResponse
                await JSONResponse({'error': 'dashboard_read_only', 'mode': READ_ONLY_GUI},
                                   status_code=403)(scope, receive, send)
                return
        await self.app(scope, receive, send)


def main(argv=None):
    import argparse, json
    from trader.core.config import load_config
    parser = argparse.ArgumentParser()
    parser.add_argument('--root', type=Path, required=True)
    args = parser.parse_args(argv)
    result = startup_check(args.root, load_config(str(args.root / 'config.yaml')))
    print(json.dumps(result, sort_keys=True))
    raise SystemExit(0 if result['allow'] else 1)


if __name__ == '__main__':
    main()
