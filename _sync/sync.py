# Rebuilds index.html for golubchenko-desserts.ru from the VK group «Десерты на любой вкус в СПб».
# Runs in GitHub Actions (.github/workflows/sync.yml).
#   wall    wall.get with the SERVICE token (secret VK_TOKEN): new clips go first in the video hall,
#           fresh photo posts fill the «Новые поступления» block
#   market  data/market.json, saved by the «Обновить сайт» button in the VK mini app (vk/) through
#           Apps Script (market.get needs a user token, so the site can't read the shop by itself).
#           Until she presses the button once, the hand-made catalog stays exactly as it is.
# Catalog rules (items.json is the hand-made catalog, mk = VK market ids behind each card):
#   price  from the market (one price, or a «min–max» range), except cards with keep_price
#   hidden when every market item behind the card is deleted or marked unavailable
#   new    a market item that no card knows becomes a card with the tag «Новинка» in a guessed hall
# Offline test: python3 sync.py --offline [--market=<file>] [--wall=<file>]
# Edit the page in _sync/index.src.html, never in index.html (it is overwritten).
import datetime, hashlib, io, json, os, re, sys, time, urllib.parse, urllib.request
from zoneinfo import ZoneInfo
from PIL import Image, ImageOps

GID = 226625031
HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
IMG = os.path.join(ROOT, 'img')
DATA = os.path.join(HERE, 'data')
MAN = os.path.join(DATA, 'manifest.json')
MSK = ZoneInfo('Europe/Moscow')
ARGS = dict(a[2:].split('=', 1) if '=' in a else (a[2:], '1') for a in sys.argv[1:] if a.startswith('--'))
OFFLINE = 'offline' in ARGS
FORCE = 'force' in ARGS
NEW_AFTER = datetime.datetime(2026, 11, 13, 0, 0, tzinfo=MSK).timestamp()  # clips after the launch snapshot
NEWS_DAYS = 30
MAX_CLIPS = 10
MON = ['января', 'февраля', 'марта', 'апреля', 'мая', 'июня', 'июля', 'августа', 'сентября', 'октября', 'ноября', 'декабря']
UA = {'User-Agent': 'Mozilla/5.0 (golubchenko-desserts site sync)'}
# hall for a new market item, first match wins
HALL_RULES = [('free', r'без сахара|в пост|постн|без выпечки'), ('bento', r'бенто|мини-торт|тортик'),
              ('pastry', r'пирожн|моти|сигар|эклер|капкейк|трюфел|макарон|тарт'),
              ('gift', r'зефир|коробоч|подар|набор|рулет|бомб|пряник|конфет'), ('mousse', r'муссов')]


class VKError(Exception):
    pass


def api(method, **params):
    token = os.environ.get('VK_TOKEN', '')
    if not token:
        sys.exit('VK_TOKEN is not set')
    params.update(access_token=token, v='5.199')
    req = urllib.request.Request('https://api.vk.com/method/' + method, data=urllib.parse.urlencode(params).encode(), headers=UA)
    data = json.loads(urllib.request.urlopen(req, timeout=30).read())
    if 'error' in data:  # never print the request: it holds the token
        e = data['error']; raise VKError(f"VK {method} error {e.get('error_code')}: {e.get('error_msg')}")
    return data['response']


def fetch(url):
    err = None
    for i in range(3):
        try:
            return urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=40).read()
        except Exception as e:  # noqa: BLE001
            err = e; time.sleep(2 * (i + 1))
    raise err


def save_img(raw, name, mx, q=78):
    im = ImageOps.exif_transpose(Image.open(io.BytesIO(raw))).convert('RGB')
    im.thumbnail((mx, mx), Image.LANCZOS)
    im.save(os.path.join(IMG, name + '.jpg'), 'JPEG', quality=q, optimize=True, progressive=True)


def biggest(sizes):
    return max((s for s in sizes if s.get('url')), key=lambda s: s.get('width', 0) * s.get('height', 0))['url']


def load(path, default=None):
    return json.load(open(path, encoding='utf-8')) if os.path.exists(path) else default


def tidy(t):
    t = re.sub(r'\[(?:club|public|id)\d+\|([^\]]*)\]', r'\1', t)
    t = re.sub(r'#\S+', '', t)
    t = re.sub('[\U0001F000-\U0001FAFF\u2300-\u23FF\u2B00-\u2BFF☀-➿️‍⠀]', '', t)
    return re.sub(r'\s+', ' ', t).strip()


def cut(t, n):
    return t if len(t) <= n else t[:n].rsplit(' ', 1)[0].rstrip(',.;:!?—–-') + '…'


def rub(n):
    return f'{n:,}'.replace(',', ' ')


def dstr(ts):
    d = datetime.datetime.fromtimestamp(ts, MSK); return f'{d.day} {MON[d.month - 1]}'


def catalog(items, market, man):
    """Apply the market to the hand-made catalog. market = list of VK market items or None."""
    if market is None:
        return items
    live = {m['id']: m for m in market if m.get('availability', 0) == 0 and m.get('price')}
    known = {i for x in items for i in x.get('mk', [])}
    out = []
    for x in items:
        x = dict(x)
        if x.get('mk'):
            ms = [live[i] for i in x['mk'] if i in live]
            if not ms:
                continue  # every market item behind the card is gone or unavailable
            if not x.get('keep_price'):
                ps = sorted({int(m['price']['amount']) // 100 for m in ms})
                x['p'] = f'{rub(ps[0])} ₽' if len(ps) == 1 else f'{rub(ps[0])}–{rub(ps[-1])} ₽'
        out.append(x)
    photos = man.setdefault('market_photos', {})
    for m in market:
        if m['id'] in known or m['id'] not in live:
            continue
        title = tidy(m['title']).rstrip('!.')
        text = tidy(m.get('description', ''))
        low = (title + ' ' + text).lower()
        hall = next((h for h, rx in HALL_RULES if re.search(rx, low)), 'cakes')
        ph = m.get('photos', [])[:4]
        ids = [p['id'] for p in ph]
        names = [f"mk-{m['id']}-{n}" for n in range(1, len(ph) + 1)]
        if ph and (photos.get(str(m['id'])) != ids or not all(os.path.exists(os.path.join(IMG, f + '.jpg')) for f in names)):
            for p, f in zip(ph, names):
                save_img(fetch(biggest(p['sizes'])), f, 1000)
            photos[str(m['id'])] = ids
            print('photos saved for new item', m['id'], title[:40])
        if not names:
            print('new item without photos skipped:', title); continue
        unit = 'за 1 кг' if re.search(r'за 1 ?кг|цена за кг|за килограмм', low) else ''
        card = {'h': hall, 't': title, 'img': names, 'p': f"{rub(int(m['price']['amount']) // 100)} ₽", 'u': unit,
                'tag': 'Новинка', 'd': cut(text, 420), 'm': '', 'mk': [m['id']], 'isnew': True}
        first = next((k for k, y in enumerate(out) if y['h'] == hall), len(out))
        out.insert(first, card)  # new items open their hall
    return out


def news_and_clips(wall, clips_seed):
    now = time.time()
    posts = []
    for p in wall:
        if p.get('marked_as_ads') or now - p['date'] > NEWS_DAYS * 86400:
            continue
        ph = [a['photo'] for a in p.get('attachments', []) if a['type'] == 'photo']
        t = tidy(p.get('text', ''))
        if not ph or len(t) < 80:
            continue  # short personal notes («Улетаю к морю!») stay in the group
        name = f"post-{ph[0]['id']}"
        if not os.path.exists(os.path.join(IMG, name + '.jpg')):
            save_img(fetch(biggest(ph[0]['sizes'])), name, 900)
        posts.append({'id': p['id'], 'date': dstr(p['date']), 'text': cut(t, 220), 'img': name})
        if len(posts) == 3:
            break
    seen, fresh = {c[1] for c in clips_seed}, []
    for p in wall:
        for a in p.get('attachments', []):
            c = a.get('clip') if a['type'] == 'clip' else None
            if not c or c['id'] in seen or c.get('date', 0) <= NEW_AFTER:
                continue
            seen.add(c['id'])
            name = f"clip-{c['id']}"
            if not os.path.exists(os.path.join(IMG, name + '.jpg')):
                tall = [i for i in c.get('image', []) if i.get('width', 0) >= 400 and i.get('height', 0) > i.get('width', 0)]
                src = [i for i in c.get('first_frame', []) if i.get('width', 0) >= 600] or tall or c.get('image', [])
                if not src:
                    continue
                save_img(fetch(max(src, key=lambda i: i.get('width', 0))['url']), name, 720, 74)
            lines = [tidy(x) for x in c.get('description', '').split('\n')]
            cap = next((x for x in lines if x), '') or 'Новый клип'
            fresh.append((c['date'], [name, c['id'], cut(cap, 80)]))
    fresh = [c for _, c in sorted(fresh, key=lambda z: -z[0])]
    return posts, (fresh + clips_seed)[:MAX_CLIPS]


def main():
    items = load(os.path.join(HERE, 'items.json'))
    clips_seed = load(os.path.join(HERE, 'clips.json'))
    man = load(MAN, {})
    mfile = ARGS.get('market') or os.path.join(DATA, 'market.json')
    mdata = load(mfile)
    market = mdata['response']['items'] if mdata else None
    if OFFLINE:
        w = load(ARGS.get('wall') or os.path.join(DATA, 'wall.json'), {'items': []})
        wall = w.get('response', w)['items']
    else:
        wall = api('wall.get', owner_id=-GID, count=40)['items']
    cat = catalog(items, market, man)
    posts, clips = news_and_clips(wall, clips_seed)
    data = {'items': cat, 'clips': clips, 'news': posts}
    h = hashlib.sha256(json.dumps(data, ensure_ascii=False, sort_keys=True).encode()).hexdigest()
    if h == man.get('hash') and not FORCE:
        json.dump(man, open(MAN, 'w', encoding='utf-8'), ensure_ascii=False)
        print('no changes'); return
    js = lambda o: json.dumps(o, ensure_ascii=False, separators=(',', ':')).replace('</', '<\\/')
    tpl = open(os.path.join(HERE, 'index.src.html'), encoding='utf-8').read()
    for k, v in (('__ITEMS__', cat), ('__CLIPS__', clips), ('__NEWS__', posts)):
        assert tpl.count(k) == 1, k
        tpl = tpl.replace(k, js(v))
    open(os.path.join(ROOT, 'index.html'), 'w', encoding='utf-8').write(tpl)
    man['hash'] = h
    json.dump(man, open(MAN, 'w', encoding='utf-8'), ensure_ascii=False)
    new = sum(1 for x in cat if x.get('isnew'))
    print(f"rebuilt: {len(cat)} cards ({new} new, {len(items) - (len(cat) - new)} hidden), "
          f"{len(posts)} posts, {len(clips)} clips, market {'from button' if market is not None else 'not used yet'}")


if __name__ == '__main__':
    main()
