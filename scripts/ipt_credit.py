"""Read-only IPT account credit evidence. Never infer completion from absence.

Authentication is reused from the existing Prowlarr indexer, never exported.
This module does not stop torrents, zap obligations, or change account state.
"""
import re
import copy
import json
import sqlite3
import gzip
import io
from contextlib import closing
import urllib.parse
import urllib.request
from decimal import Decimal
from html.parser import HTMLParser


class CreditUnavailable(ValueError):
    """Messages are fixed safe categories, never response bodies or URLs."""


class AccountPage(HTMLParser):
    def __init__(self):
        super().__init__()
        self.text, self.rows, self.profiles = [], [], set()
        self.row = self.cell = None
        self.depth = 0
        self.skip = 0

    def finish_cell(self):
        if self.cell is not None:
            self.cell['text'] = ' '.join(self.cell['text'].split())
            if self.row is not None: self.row.append(self.cell)
            self.cell = None

    def finish_row(self):
        self.finish_cell()
        if self.row is not None: self.rows.append(self.row)
        self.row = None

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag in ('script', 'style'):
            self.skip += 1
        if tag == 'a':
            href = a.get('href', '')
            match = re.fullmatch(r'/u/(\d+)', href)
            if match: self.profiles.add(match[1])
            if self.cell is not None:
                self.cell['links'].append(href)
        if tag == 'table': self.depth += 1
        # IPT's source omits most </td> and </tr> tags. HTMLParser does not
        # provide browser-style implicit closure, so implement it explicitly.
        if tag == 'tr':
            self.finish_row()
            self.row = []
        if tag in ('td', 'th') and self.row is not None:
            self.finish_cell()
            self.cell = {'text': '', 'links': []}

    def handle_endtag(self, tag):
        if tag in ('script', 'style'): self.skip = max(0, self.skip - 1)
        if tag in ('td', 'th'): self.finish_cell()
        if tag in ('tr', 'table'): self.finish_row()
        if tag == 'table': self.depth = max(0, self.depth - 1)

    def handle_data(self, data):
        if self.skip: return
        self.text.append(data)
        if self.cell is not None: self.cell['text'] += data


def torrent_id(link):
    """Only identity-bearing site links, not fuzzy title matches."""
    p = urllib.parse.urlsplit(link)
    if p.netloc and p.hostname != 'iptorrents.com': return None
    m = re.fullmatch(r'/t/(\d+)', p.path)
    if m: return m[1]
    q = urllib.parse.parse_qs(p.query)
    if p.path in ('', '/peers', '/details.php') and len(q.get('t', [])) == 1:
        return q['t'][0] if q['t'][0].isdigit() else None
    return None


def byte_interval(text):
    """Rounded site display bounds; a displayed ratio near 1 is not proof."""
    m = re.fullmatch(r'(\d+(?:\.\d+)?)\s*(B|kB|KB|MB|GB|TB)', text)
    if not m: raise CreditUnavailable('unsupported IPT credit amount format')
    value = Decimal(m[1])
    unit = Decimal(1024) ** ('B', 'KB', 'MB', 'GB', 'TB').index(m[2].upper())
    resolution = Decimal(10) ** value.as_tuple().exponent
    return max(Decimal(0), value - resolution / 2) * unit, (value + resolution / 2) * unit


def parse_required(html):
    page = AccountPage(); page.feed(html)
    text = ' '.join(' '.join(page.text).split())
    count = re.search(r'H&R Torrents:\s*(\d+)', text)
    header = ['Torrent', 'Uploaded', 'Downloaded', 'Gap', 'Seeding Time', 'Zap (Delete 1 Hit & Run)']
    if not page.profiles or not count or not any([c['text'] for c in r] == header for r in page.rows):
        raise CreditUnavailable('IPT account page unavailable or unsupported; retirement held')
    records = {}
    for row in page.rows:
        if len(row) != 6: continue
        ids = {torrent_id(link) for link in row[0]['links']} - {None}
        if not ids: continue
        if len(ids) != 1: raise CreditUnavailable('ambiguous IPT torrent identity')
        tid = ids.pop()
        if tid in records: raise CreditUnavailable('duplicate IPT credit record')
        # The source appends a CSS-hidden tooltip with time since last seen.
        # It is not additional credited seeding time.
        hours = re.fullmatch(r'(\d+\.\d+) hours(?:(\d+\.\d+) hours ago)?', row[4]['text'])
        if not hours: raise CreditUnavailable('unsupported IPT seed time format')
        uploaded = byte_interval(row[1]['text'])
        downloaded = byte_interval(row[2]['text'])
        # The display rounds to tenths. Require the lower bound to reach 336h.
        lower_hours = max(Decimal(0), Decimal(hours[1]) - Decimal('.05'))
        records[tid] = {'title': row[0]['text'], 'credited_hours_lower': float(lower_hours),
            'ratio_confirmed': downloaded[0] > 0 and uploaded[0] >= downloaded[1],
            'last_seen_age_hours': float(hours[2]) if hours[2] else None}
    if len(records) != int(count[1]):
        raise CreditUnavailable('IPT credit inventory incomplete or paginated; retirement held')
    return {'records': records, 'vip_notice': 'VIP members and members' in text,
            'account_user_ids': sorted(page.profiles)}


def eligible(record, fully_downloaded):
    """Positive account evidence only; VIP/empty/missing record is not evidence."""
    return bool(record and (record['ratio_confirmed'] or
        fully_downloaded and record['credited_hours_lower'] >= 336))


def identity_map(history):
    """Map only an arr grab's exact hash and identity-bearing IPT info URL."""
    candidates={}
    for record in history:
        if record.get('eventType') not in (1, 'grabbed'): continue
        data=record.get('data') or {}
        h=(record.get('downloadId') or data.get('torrentInfoHash') or '').lower()
        if not re.fullmatch(r'[a-f0-9]{40}',h): continue
        for field in ('nzbInfoUrl','guid'):
            url=data.get(field,'')
            p=urllib.parse.urlsplit(url)
            if p.hostname not in ('iptorrents.com','www.iptorrents.com'): continue
            tid=torrent_id(url.replace('://www.iptorrents.com/', '://iptorrents.com/'))
            if tid: candidates.setdefault(h,set()).add(tid)
    # Collisions must remain unknown, not be resolved by newest record or title.
    return {h:next(iter(ids)) for h,ids in candidates.items() if len(ids)==1}


def advance_credit(previous, records, identities, torrents, healthy, now, satisfied=None):
    """Track credited progress independently of healthy tracker announcements.

    No retirement is executed here. Missing records retain prior evidence for
    investigation but can never be promoted to completion by disappearance.
    """
    state=copy.deepcopy(previous or {})
    findings=[]
    missing_identity=missing_credit=stagnant=regressed=0
    active={'uploading','stalledUP','forcedUP'}
    satisfied=satisfied or set()
    for t in torrents:
        h=t['hash'].lower()
        if 'IPT' not in {x.strip() for x in t.get('tags','').split(',')}: continue
        if h in satisfied: continue # Exact, explicitly accepted site waiver or proven retirement.
        tid=identities.get(h)
        if not tid:
            missing_identity+=1
            continue
        r=records.get(tid)
        old=state.get(h)
        if not r:
            missing_credit+=1
            if old: old['present']=False
            continue
        hours=r['credited_hours_lower']
        if not old or old.get('torrent_id')!=tid:
            old={'torrent_id':tid,'hours':hours,'progress_at':now,'healthy_since':None}
            state[h]=old
        if hours < old['hours']:
            regressed+=1
            old['progress_at']=now
        elif hours > old['hours']:
            old['progress_at']=now
        old.update(hours=hours,present=True,observed_at=now,ratio_confirmed=r['ratio_confirmed'])
        if h in healthy and t.get('state') in active and t.get('progress')==1:
            if old.get('healthy_since') is None: old['healthy_since']=now
            # Give the recovered account two hours to update; don't mistake old
            # historical rows for proof that today's newly resumed seeds fail.
            if now-max(old['healthy_since'],old['progress_at']) > 7200 and not eligible(r,True):
                stagnant+=1
        else:
            old['healthy_since']=None
    for count,reason in [(missing_identity,'lack an unambiguous IPT identity'),
                         (missing_credit,'lack positive IPT credit evidence (blank/VIP-purged is not completion)'),
                         (stagnant,'announce successfully but IPT credited seed time has not advanced for over 2 hours'),
                         (regressed,'have regressed IPT credit counters')]:
        if count: findings.append(f'IPT retirement verification: {count} torrent(s) {reason}; retirement held')
    return state,findings


class SameOriginRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        target,source=urllib.parse.urlsplit(newurl),urllib.parse.urlsplit(req.full_url)
        if target.scheme != 'https' or target.netloc != source.netloc:
            raise CreditUnavailable('IPT changed account origin; credentials not forwarded')
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def read_account_body(response):
    """Bound both transfer and decompression; IPT sometimes sends gzip anyway."""
    limit=2_000_000
    body=response.read(limit+1)
    if len(body)>limit: raise CreditUnavailable('IPT account page exceeds validated size')
    if body[:2]==b'\x1f\x8b':
        try:
            with gzip.GzipFile(fileobj=io.BytesIO(body)) as compressed:
                body=compressed.read(limit+1)
        except Exception:
            raise CreditUnavailable('IPT compressed account page unreadable') from None
        if len(body)>limit: raise CreditUnavailable('IPT account page exceeds validated size')
    return body


def with_stored_credentials(indexers, dbpath='/home/pi/docker-configs/prowlarr/config/prowlarr.db'):
    """Prowlarr GET masks secrets. Resolve only IPT's cookie via readonly DB.

    Uses the existing indexer identity, never edits Prowlarr or persists cookies.
    SQLite's read-only connection includes current WAL transactions.
    """
    result=copy.deepcopy(indexers)
    for indexer in result:
        if 'iptorrents' not in (indexer.get('name','')+' '+indexer.get('definitionName','')).lower():
            continue
        for field in indexer.get('fields',[]):
            value=field.get('value')
            if field['name'] != 'cookie' or not isinstance(value,str) or set(value) != {'*'}:
                continue
            try:
                with closing(sqlite3.connect('file:'+urllib.parse.quote(dbpath,safe='/')+'?mode=ro',uri=True)) as db:
                    row=db.execute('SELECT Settings FROM Indexers WHERE Id=?',(indexer['id'],)).fetchone()
                cookie=json.loads(row[0])['cookie'] if row else None
                if not isinstance(cookie,str) or '=' not in cookie:
                    raise ValueError()
                field['value']=cookie
            except Exception:
                raise CreditUnavailable('IPT stored account authentication unreadable; retirement held') from None
    return result


def fetch_account_page(indexers,path):
    matches = [i for i in indexers if 'iptorrents' in
        (i.get('name', '') + ' ' + i.get('definitionName', '')).lower()]
    if len(matches) != 1: raise CreditUnavailable('IPT account connection missing or ambiguous')
    fields = {f['name']: f.get('value') for f in matches[0].get('fields', [])}
    base = fields.get('baseUrl')
    if isinstance(base, list): base = base[0] if len(base) == 1 else None
    p = urllib.parse.urlsplit(base or '')
    # Validated against this canonical origin; other mirror formats must be tested.
    if p.scheme != 'https' or p.hostname != 'iptorrents.com' or p.username or p.password or p.port:
        raise CreditUnavailable('unsupported IPT account origin; retirement held')
    cookie = fields.get('cookie')
    if not isinstance(cookie, str) or not cookie.strip():
        raise CreditUnavailable('IPT account authentication missing; retirement held')
    headers = {'Cookie': cookie, 'User-Agent': fields.get('userAgent') or 'pipeline-canary'}
    try:
        op = urllib.request.build_opener(SameOriginRedirect())
        with op.open(urllib.request.Request('https://iptorrents.com'+path, headers=headers), timeout=30) as r:
            if 'login' in urllib.parse.urlsplit(r.geturl()).path:
                raise CreditUnavailable('IPT account login expired/rejected; refresh Prowlarr IPT cookie; retirement held')
            body = read_account_body(r)
        return body.decode('utf-8','strict')
    except CreditUnavailable:
        raise
    except Exception:
        raise CreditUnavailable('IPT account credit read failed; retirement held') from None


def fetch_required(indexers):
    return parse_required(fetch_account_page(indexers,'/seeding_required.php'))


def parse_account_ratio(html,user_id):
    """Return only exact account totals. Never expose the profile's passkey."""
    page=AccountPage();page.feed(html)
    text=' '.join(' '.join(page.text).split())
    # The exact byte totals follow their label on the VIP profile ("Uploaded
    # 426.45 GB 457,899,577,111 B") and precede it on the plain one. Exactly one
    # layout must read both; a page matching both is ambiguous.
    layouts=[[re.findall(r'(\d[\d,]*) B '+label+r'\b',text,re.I) for label in ('Uploaded','Downloaded')],
             [re.findall(r'\b'+label+r' [\d.,]+ [KMGTP]?i?B (\d[\d,]*) B\b',text,re.I) for label in ('Uploaded','Downloaded')]]
    readable=[(up,down) for up,down in layouts if len(up)==1 and len(down)==1]
    if page.profiles!={str(user_id)} or len(readable)!=1:
        raise CreditUnavailable('IPT account ratio format unavailable or unsupported')
    up,down=(int(n[0].replace(',','')) for n in readable[0])
    return {'uploaded':up,'downloaded':down,'below_minimum':up<down}


def fetch_account_ratio(indexers,user_id):
    if not str(user_id).isdigit(): raise CreditUnavailable('IPT account identity unsupported')
    return parse_account_ratio(fetch_account_page(indexers,'/user.php?u='+str(user_id)),user_id)
