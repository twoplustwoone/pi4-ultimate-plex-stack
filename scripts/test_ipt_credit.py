import unittest
import gzip
import io
import json
import sqlite3
import tempfile
from contextlib import closing
from pathlib import Path
from unittest.mock import patch
import ipt_credit as c


def page(hours='232.1', up='0.00 kB', down='11.46 GB', count=1, rows=True):
    header='<tr>'+''.join('<th>'+x+'</th>' for x in
        ['Torrent','Uploaded','Downloaded','Gap','Seeding Time','Zap (Delete 1 Hit & Run)'])+'</tr>'
    row=(f'<tr><td><a href="/t/1500917">Example</a></td><td>{up}</td><td>{down}</td>'
         f'<td>11 GB</td><td>{hours} hours</td><td><button>Zap</button></td></tr>') if rows else ''
    return f'<a href="/u/123">User</a>H&amp;R Torrents: {count}<table>{header}{row}</table>'


class CreditTests(unittest.TestCase):
    def test_account_ratio_exact_totals_without_profile_secrets(self):
        html='<a href="/u/123">User</a>361.25 GB 387,888,345,393 B UPLOADED 656.74 GB 705,164,665,385 B DOWNLOADED Security Passkey secret'
        result=c.parse_account_ratio(html,'123')
        self.assertTrue(result['below_minimum'])
        self.assertEqual(c.parse_account_ratio(html.replace('UPLOADED','Uploaded').replace('DOWNLOADED','Downloaded'),'123'),result)
        self.assertNotIn('secret',str(result))
        self.assertFalse(c.parse_account_ratio(html.replace('387,888,345,393','999,888,345,393'),'123')['below_minimum'])
        with self.assertRaises(c.CreditUnavailable): c.parse_account_ratio(html,'456')
        with self.assertRaises(c.CreditUnavailable): c.parse_account_ratio(html.replace('UPLOADED','unknown'),'123')

    def test_bounded_plain_and_gzip_pages(self):
        body=page().encode()
        for raw in (body,gzip.compress(body)):
            self.assertEqual(c.read_account_body(io.BytesIO(raw)),body)
        with self.assertRaises(c.CreditUnavailable):
            c.read_account_body(io.BytesIO(gzip.compress(b'a'*2_000_001)))
        with self.assertRaises(c.CreditUnavailable):
            c.read_account_body(io.BytesIO(b'a'*2_000_001))

    def test_credential_redirect_cannot_downgrade_https(self):
        request=c.urllib.request.Request('https://iptorrents.com/seeding_required.php',headers={'Cookie':'secret'})
        with self.assertRaises(c.CreditUnavailable):
            c.SameOriginRedirect().redirect_request(request,None,302,'',{},'http://iptorrents.com/login')

    def test_real_site_omitted_table_end_tags(self):
        html='<table><tr><td>'+page().replace('</td>','').replace('</tr>','')+'</td></tr></table>'
        self.assertEqual(len(c.parse_required(html)['records']),1)

    def test_last_seen_tooltip_is_not_credited_seed_time(self):
        html=page().replace('232.1 hours','232.1 hours<div class="tTip ttop">197.8 hours ago</div>')
        r=c.parse_required(html)['records']['1500917']
        self.assertAlmostEqual(r['credited_hours_lower'],232.05)
        self.assertEqual(r['last_seen_age_hours'],197.8)

    def test_masked_api_cookie_uses_readonly_stored_settings(self):
        with tempfile.TemporaryDirectory() as folder:
            path=str(Path(folder)/'prowlarr.db')
            with closing(sqlite3.connect(path)) as db:
                db.execute('CREATE TABLE Indexers(Id INTEGER,Settings TEXT)')
                db.execute('INSERT INTO Indexers VALUES (?,?)',(7,json.dumps({'cookie':'uid=real; pass=secret'})))
                db.commit()
            original=[{'id':7,'name':'IPTorrents','fields':[{'name':'cookie','value':'********'}]}]
            restored=c.with_stored_credentials(original,path)
            self.assertEqual(restored[0]['fields'][0]['value'],'uid=real; pass=secret')
            self.assertEqual(original[0]['fields'][0]['value'],'********')

    def test_real_credit_example(self):
        r=c.parse_required(page())['records']['1500917']
        self.assertAlmostEqual(r['credited_hours_lower'],232.05)
        self.assertFalse(c.eligible(r,True))

    def test_time_completion_and_partial_exemption(self):
        r=c.parse_required(page(hours='336.1'))['records']['1500917']
        self.assertTrue(c.eligible(r,True))
        self.assertFalse(c.eligible(r,False))

    def test_rounding_not_completion(self):
        r=c.parse_required(page(hours='336.0',up='11.46 GB'))['records']['1500917']
        self.assertFalse(c.eligible(r,True))

    def test_ratio_can_complete_partial(self):
        r=c.parse_required(page(up='11.48 GB'))['records']['1500917']
        self.assertTrue(c.eligible(r,False))

    def test_empty_or_missing_is_not_completion(self):
        self.assertFalse(c.eligible(None,True))
        self.assertEqual(c.parse_required(page(count=0,rows=False))['records'],{})

    def test_login_and_unsupported_pages(self):
        for html in ['<h1>Sign in</h1>',page().replace('Seeding Time','New label'),
                     page(hours='yesterday'),page(down='unknown'),page(count=2)]:
            with self.subTest(html=html):
                with self.assertRaises(c.CreditUnavailable): c.parse_required(html)

    def test_duplicate_identity(self):
        html=page(count=2)
        row=html[html.index('<tr><td>'):html.index('</table>')]
        with self.assertRaises(c.CreditUnavailable): c.parse_required(html.replace('</table>',row+'</table>'))

    def test_exact_identity_only(self):
        self.assertEqual(c.torrent_id('?t=123'),'123')
        self.assertEqual(c.torrent_id('/t/123'),'123')
        for link in ['/t/movie-title','https://evil.example/t/123','?t=123&t=456']:
            self.assertIsNone(c.torrent_id(link))

    def test_no_cookie_or_ambiguous_indexer(self):
        for indexers in [[],[{'name':'IPTorrents','fields':[]}],
                         [{'name':'IPTorrents'},{'name':'IPTorrents'}]]:
            with self.assertRaises(c.CreditUnavailable): c.fetch_required(indexers)

    def test_expired_session_is_safe_error(self):
        class Response:
            def __enter__(self): return self
            def __exit__(self,*a): pass
            def geturl(self): return 'https://iptorrents.com/login.php'
        class Opener:
            def open(self,*a,**k): return Response()
        indexers=[{'name':'IPTorrents','fields':[
            {'name':'baseUrl','value':'https://iptorrents.com/'},
            {'name':'cookie','value':'secret=credential'}]}]
        with patch('urllib.request.build_opener',return_value=Opener()):
            with self.assertRaises(c.CreditUnavailable) as error: c.fetch_required(indexers)
        self.assertIn('login expired',str(error.exception))
        self.assertNotIn('credential',str(error.exception))

    def test_no_credential_forwarding_to_other_origin(self):
        request=c.urllib.request.Request('https://iptorrents.com/seeding_required.php',headers={'Cookie':'secret'})
        with self.assertRaises(c.CreditUnavailable):
            c.SameOriginRedirect().redirect_request(request,None,302,'',{},'https://evil.example/login')

    def test_hash_identity_from_history_not_titles(self):
        h='a'*40
        record={'eventType':'grabbed','downloadId':h.upper(),'sourceTitle':'Misleading title',
                'data':{'nzbInfoUrl':'https://iptorrents.com/t/123'}}
        self.assertEqual(c.identity_map([record]),{h:'123'})
        other=dict(record,data={'nzbInfoUrl':'https://iptorrents.com/t/456'})
        self.assertEqual(c.identity_map([record,other]),{})
        self.assertEqual(c.identity_map([dict(record,eventType='downloadFolderImported')]),{})

    def test_credit_stagnation_requires_healthy_observation_window(self):
        h='a'*40
        t={'hash':h,'tags':'IPT, private','progress':1,'state':'stalledUP'}
        record={'credited_hours_lower':200,'ratio_confirmed':False}
        s,errors=c.advance_credit({}, {'123':record},{h:'123'},[t],{h},100)
        self.assertFalse(errors)
        _,errors=c.advance_credit(s, {'123':record},{h:'123'},[t],{h},7301)
        self.assertIn('not advanced',errors[0])
        # Newly credited time clears the stagnation failure, even with no upload.
        record=dict(record,credited_hours_lower=201)
        _,errors=c.advance_credit(s, {'123':record},{h:'123'},[t],{h},7301)
        self.assertFalse(errors)

    def test_credit_missing_and_regression_do_not_complete(self):
        h='a'*40
        t={'hash':h,'tags':'IPT','progress':1,'state':'stalledUP'}
        record={'credited_hours_lower':200,'ratio_confirmed':False}
        s,_=c.advance_credit({}, {'123':record},{h:'123'},[t],{h},100)
        absent,errors=c.advance_credit(s, {},{h:'123'},[t],{h},200)
        self.assertFalse(absent[h]['present'])
        self.assertIn('lack positive',errors[0])
        self.assertTrue(s[h]['present']) # pure candidate state; report cannot mutate caller
        _,errors=c.advance_credit(s, {'123':dict(record,credited_hours_lower=1)},{h:'123'},[t],{h},200)
        self.assertIn('regressed',errors[0])

    def test_unhealthy_or_partial_does_not_accrue_healthy_window(self):
        h='a'*40
        t={'hash':h,'tags':'IPT','progress':.5,'state':'downloading'}
        record={'credited_hours_lower':200,'ratio_confirmed':False}
        s,_=c.advance_credit({}, {'123':record},{h:'123'},[t],{h},100)
        self.assertIsNone(s[h]['healthy_since'])


if __name__=='__main__': unittest.main()
