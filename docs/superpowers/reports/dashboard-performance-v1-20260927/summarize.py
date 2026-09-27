"""Summarize before/after JSON results into the report tables (stdout)."""
import json, statistics
from pathlib import Path
D = Path(__file__).parent
def load(p):
    try: return json.loads((D / p).read_text())
    except (OSError, ValueError): return None
print('| Scenario | Metric | BEFORE runs (ms) | AFTER runs (ms) |\n|---|---|---|---|')
names = ['cold_offline_dependencies', 'warm_browser', 'delayed_parser_script', 'slow_serial_network']
for n in names:
    for metric, key in (('usable navigation', 'navigation_and_composer_ms'), ('first useful local data', 'first_useful_local_data_ms')):
        cells = []
        for mode in ('before', 'after'):
            vals = []
            for r in (1, 2, 3):
                d = load(f'{mode}-scenarios-r{r}.json')
                row = next((x for x in (d or {}).get('scenarios', []) if x['name'] == n), None)
                if row: vals.append(row[key])
            cells.append(' / '.join(f'{v:,.0f}' for v in vals) + (f' (median {statistics.median(vals):,.0f})' if vals else '') if vals else 'n/a')
        print(f'| {n} | {metric} | {cells[0]} | {cells[1]} |')
for mode in ('before', 'after'):
    for r in (1, 2, 3):
        d = load(f'{mode}-scenarios-r{r}.json')
        for x in (d or {}).get('scenarios', []):
            if x['name'] == 'slow_serial_network':
                print(mode, r, 'slow: max concurrent fetches', (x.get('fetch') or {}).get('maxActive'), 'nav_during_stall_ms', round(x.get('nav_during_stall_ms') or 0), 'wallet_ms', x.get('wallet_enrichment_ms'), 'server', x.get('server') if isinstance(x.get('server'), str) else (x.get('server') or {}).get('network_model'))
for mode in ('before', 'after'):
    d = load(f'{mode}-behaviours.json')
    for b in (d or {}).get('behaviours', []): print(mode, json.dumps(b))
for mode in ('before', 'after'):
    d = load(f'{mode}-api.json')
    if not d: continue
    print(f'\n{mode} endpoints (cold ms, warm p95 ms, bytes):')
    for k, v in d['endpoints'].items(): print(f'  {k}: {v["cold_ms"]} / {v["warm_p95_ms"]} / {v["bytes"]} {v["statuses"]}')
    print(mode, 'during_stall', json.dumps(d['during_stall']))
    for w in ('idle', 'refresh_load'):
        s = d[w]['server']; print(mode, w, 'cpu_s', round(s[1]['cpu_s'] - s[0]['cpu_s'], 2), 'rss_mib', round(s[1]['rss_mib'], 1), 'core', json.dumps(d[w]['core_probe']), 'round_p95', d[w].get('round_p95_ms'))
    print(mode, 'checkpoint', d['passive_checkpoint'], 'threads', d['server_resource_end'].get('threads'))
