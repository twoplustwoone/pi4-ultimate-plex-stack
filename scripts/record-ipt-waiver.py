#!/usr/bin/env python3
"""Record a human-approved, documented IPT waiver for exact existing seeds.

Run once on yggdrasil after checking the site message and obtaining approval.
Does not stop torrents. Future or re-added incarnations cannot inherit waiver.
"""
import argparse
import fcntl
import http.cookiejar
import json
import os
import socket
import subprocess
import time
import urllib.parse
import urllib.request
from datetime import datetime
from ipt_credit import fetch_required,with_stored_credentials
from tracker_health import load_state,save_state
from torrent_retirement import initial_retirement,incarnation


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--cutoff',required=True)
    parser.add_argument('--source',required=True)
    parser.add_argument('--user-id',required=True)
    parser.add_argument('--apply-approved-waiver',action='store_true')
    args=parser.parse_args()
    if socket.gethostname()!='yggdrasil': raise ValueError('wrong host')
    cutoff=datetime.fromisoformat(args.cutoff.replace('Z','+00:00')).timestamp()
    now=time.time()
    if not 0<cutoff<=now: raise ValueError('waiver cutoff must be a past timestamp')
    source=urllib.parse.urlsplit(args.source)
    if source.scheme!='https' or source.netloc!='iptorrents.com' or source.query or source.fragment \
        or not source.path.startswith('/inbox/') or not source.path[7:].isdigit():
        raise ValueError('source must be the verified IPT system conversation URL')
    root='/home/twoplustwoone'
    path=root+'/.canary-tracker-health.json'
    with open(path+'.lock','a') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        state=load_state(path)
        if state.get('retirement',{}).get('waiver'): raise ValueError('waiver already recorded; refusing overwrite')
        key=subprocess.check_output(['docker','exec','prowlarr','sed','-n',
            r's#.*<ApiKey>\(.*\)</ApiKey>.*#\1#p','/config/config.xml'],stderr=subprocess.DEVNULL).decode().strip()
        req=urllib.request.Request('http://127.0.0.1:9696/api/v1/indexer',headers={'X-Api-Key':key})
        with urllib.request.urlopen(req,timeout=30) as r: indexers=json.load(r)
        credit=fetch_required(with_stored_credentials(indexers))
        if credit['account_user_ids']!=[args.user_id] or credit['records']:
            raise ValueError('account identity or cleared H&R inventory not verified')
        with open(root+'/pi4-ultimate-plex-stack/.env') as f:
            env=dict(line.strip().split('=',1) for line in f if '=' in line and not line.startswith('#'))
        op=urllib.request.build_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))
        base='http://127.0.0.1:8080'
        form=urllib.parse.urlencode({'username':env['QBITTORRENT_USER'].strip('"'),
            'password':env['QBITTORRENT_PASS'].strip('"')}).encode()
        with op.open(urllib.request.Request(base+'/api/v2/auth/login',data=form,headers={'Referer':base}),timeout=30) as r:
            if r.read()!=b'Ok.': raise ValueError('qBittorrent login refused')
        with op.open(base+'/api/v2/torrents/info',timeout=30) as r: torrents=json.load(r)
        hashes={t['hash']:incarnation(t) for t in torrents if 'IPT' in {x.strip() for x in t.get('tags','').split(',')}
            and t.get('progress')==1 and 0<t.get('added_on',0)<=t.get('completion_on',0)<=cutoff}
        if not hashes: raise ValueError('no eligible pre-donation completed seeds')
        retirement=initial_retirement()
        retirement.update(enabled=True,waiver={'cutoff':cutoff,'source':args.source,'approved_at':now,
            'account_user_id':args.user_id,'hashes':hashes})
        print('Verified cleared account and %d exact pre-donation completed seeds; no stops performed.'%len(hashes))
        if args.apply_approved_waiver:
            backup=root+'/pi4-ultimate-plex-stack/.local/retirement-before-waiver-'+str(int(now))+'.json'
            save_state(backup,state)
            state['retirement']=retirement
            save_state(path,state)
            print('Accepted waiver recorded; new 24-hour observed buffer starts on the next canary apply.')


if __name__=='__main__':
    try: main()
    except Exception as e:
        print('Waiver recording refused: '+type(e).__name__)
        raise SystemExit(1)
