import json, os, sys, time, urllib.request
from datetime import datetime, timedelta, timezone

ROOT = os.path.dirname(os.path.abspath(__file__))
FRONTEND = os.path.join(ROOT, 'frontend')
HISTORY = os.path.join(FRONTEND, 'history')
DHAKA = timezone(timedelta(hours=6))

API_BASE = 'https://sharedealnow.com/api/v1'
HEADERS = {
    'User-Agent': 'Dart/3.12 (dart:io)',
    'Accept': 'application/json',
    'Accept-Encoding': 'gzip',
    'Content-Type': 'application/json',
    'Authorization': 'Bearer',
}
PAGE_DELAY = 0.5

def to_float(v):
    if v is None:
        return None
    try:
        return float(str(v).replace(',', '').strip())
    except Exception:
        return None

def today_str():
    return datetime.now(DHAKA).date().isoformat()

def api_request(path, body=None, retries=4):
    url = API_BASE + path
    if body is not None:
        req = urllib.request.Request(url, data=json.dumps(body).encode('utf-8'), headers=HEADERS, method='POST')
    else:
        req = urllib.request.Request(url, headers=HEADERS, method='GET')
    for attempt in range(retries):
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                raw = resp.read()
                if resp.headers.get('Content-Encoding') == 'gzip':
                    import gzip
                    raw = gzip.decompress(raw)
                return json.loads(raw.decode('utf-8'))
        except (urllib.error.HTTPError, urllib.error.URLError, OSError) as e:
            wait = 3 * (2 ** attempt)
            print(f'  retry {attempt + 1}/{retries} ({path}) after {wait}s — {e}')
            time.sleep(wait)
    raise RuntimeError(f'API failed after {retries} tries: {url}')

def fetch_all_products(path, post=True):
    results = []
    page = 1
    while True:
        if post:
            data = api_request(path, {'page': str(page)})
        else:
            data = api_request(f'{path}?page={page}')
        plist = data.get('products')
        items = plist.get('data') if isinstance(plist, dict) else (plist if isinstance(plist, list) else [])
        results.extend([p for p in items if isinstance(p, dict)])
        last_page = plist.get('last_page') if isinstance(plist, dict) else 1
        current = plist.get('current_page') if isinstance(plist, dict) else page
        print(f'  {path} page {current}/{last_page}: +{len(items)} products')
        if not isinstance(plist, dict) or current >= last_page or not items:
            break
        page += 1
        time.sleep(PAGE_DELAY)
    return results

def group_price(product):
    gp = product.get('group_prices') or []
    vals = [to_float(p.get('price')) for p in gp]
    vals = [v for v in vals if v is not None]
    return min(vals) if vals else None

def history_entry(product, date):
    return {
        'date': date,
        'price': to_float(product.get('price')),
        'offer': to_float(product.get('offer_price')) if product.get('is_discounted') else None,
        'group': group_price(product),
        'stock': to_float(product.get('stock')),
    }

def product_meta(product):
    cat = product.get('category') or {}
    return {
        'id': product.get('id'),
        'name': product.get('name'),
        'slug': product.get('slug'),
        'sku': product.get('sku'),
        'category_id': product.get('category_id'),
        'category': cat.get('name') if isinstance(cat, dict) else None,
        'unit': product.get('unit'),
        'volume': product.get('volume'),
        'image': product.get('image'),
        'best_selling': product.get('best_selling_product'),
    }

def load_master():
    path = os.path.join(FRONTEND, 'sharedeal_products.json')
    if os.path.exists(path):
        with open(path, 'r', encoding='utf-8') as f:
            return json.load(f)
    return {'store': 'ShareDeal', 'api_base': API_BASE, 'categories': [], 'products': {}}

def load_categories():
    data = api_request('/home')
    cats = []
    for c in data.get('allCategories', []):
        cats.append({
            'id': c.get('id'),
            'name': c.get('name'),
            'slug': c.get('slug'),
            'featured': c.get('featured'),
            'image': c.get('image'),
        })
    return sorted({c['id']: c for c in cats}.values(), key=lambda c: c['id'])

def merge_day(master, products, date):
    master_products = master.setdefault('products', {})
    today_added = 0
    today_updated = 0
    for pid, p in products.items():
        entry = history_entry(p, date)
        mp = master_products.get(str(pid))
        if mp is None:
            meta = product_meta(p)
            meta['history'] = [entry]
            master_products[str(pid)] = meta
            today_added += 1
            continue
        mp.update({k: v for k, v in product_meta(p).items() if v is not None})
        hist = mp.get('history', [])
        for i, h in enumerate(hist):
            if h['date'] == date:
                hist[i] = entry
                today_updated += 1
                break
        else:
            hist.append(entry)
            mp['history'] = sorted(hist, key=lambda h: h['date'])
            today_added += 1
    return today_added, today_updated

def summarize_changes(master, date):
    products = master.get('products', {})
    prev_dates = sorted({h['date'] for p in products.values() for h in p.get('history', [])})
    prev_date = None
    for d in prev_dates:
        if d < date:
            prev_date = d
    if prev_date is None:
        return None
    changed = []
    for pid, p in products.items():
        hist = p.get('history', [])
        cur = next((h for h in hist if h['date'] == date), None)
        prev = next((h for h in hist if h['date'] == prev_date), None)
        if not cur or not prev:
            continue
        diffs = []
        for field in ('price', 'offer', 'group'):
            a, b = prev.get(field), cur.get(field)
            if a is not None and b is not None and abs(a - b) > 0.001:
                diffs.append(f'{field}: {a} -> {b}')
        if diffs:
            changed.append((p.get('name'), diffs))
    return prev_date, changed

def save_master(master):
    os.makedirs(FRONTEND, exist_ok=True)
    json_path = os.path.join(FRONTEND, 'sharedeal_products.json')
    with open(json_path, 'w', encoding='utf-8') as f:
        json.dump(master, f, indent=2, ensure_ascii=False)
    js_path = os.path.join(FRONTEND, 'sharedeal_data.js')
    with open(js_path, 'w', encoding='utf-8') as f:
        f.write('window.sharedeal_data = ')
        json.dump(master, f, ensure_ascii=False)
        f.write(';\n')
    return json_path

def run_once():
    date = today_str()
    print(f'ShareDeal scraper — {date}')
    master = load_master()
    print(f'Existing master: {len(master.get("products", {}))} products')

    print('Fetching categories (/home)...')
    cats = load_categories()
    print(f'  {len(cats)} categories')

    all_products = []
    for path, post in (('/products', True), ('/group-buy/products', False), ('/deal-buy/products', False)):
        try:
            all_products += fetch_all_products(path, post)
        except Exception as e:
            print(f'  {path} ERROR: {e}')

    products = {}
    for p in all_products:
        if p.get('id') is None:
            continue
        products[int(p['id'])] = p
    print(f'Fetched {len(products)} unique products')

    master['categories'] = cats
    master['captured_at'] = date
    added, updated = merge_day(master, products, date)
    print(f'History: {added} new, {updated} updated on {date}')

    summary = summarize_changes(master, date)
    if summary:
        prev_date, changed = summary
        print(f'Price changes vs {prev_date}: {len(changed)} products')
        for name, diffs in changed[:10]:
            print(f'  {name[:60]}: {" | ".join(diffs)}')
    else:
        print('No previous snapshot to compare (first recorded day)')

    json_path = save_master(master)
    os.makedirs(HISTORY, exist_ok=True)
    snapshot_path = os.path.join(HISTORY, f'sharedeal_products_{date}.json')
    today_snapshot = {pid: {**product_meta(p), 'history': [h for h in p.get('history', []) if h['date'] == date]}
                      for pid, p in master['products'].items()}
    with open(snapshot_path, 'w', encoding='utf-8') as f:
        json.dump(today_snapshot, f, indent=2, ensure_ascii=False)
    print(f'Saved: {json_path}')
    print(f'Saved: {snapshot_path}')
    return master

def main():
    watch = None
    if len(sys.argv) > 1 and sys.argv[1] == '--watch':
        watch = int(sys.argv[2]) if len(sys.argv) > 2 else 60
    while True:
        run_once()
        if watch is None:
            break
        print(f'\nWatching — next scrape in {watch} min (Ctrl+C to stop)')
        time.sleep(watch * 60)

if __name__ == '__main__':
    main()