"""Finite seeding policy. Pure decisions; callers persist intent before stop.

Private cleanup is deliberately separate and remains held. An accepted waiver
is bound to exact torrent incarnations, not a tag, title, VIP flag or absence.
"""
import copy
import math
from ipt_credit import eligible
from tracker_health import private_torrent, tags, sanitize

BUFFER_SECONDS=86400
MAX_OBSERVATION_GAP=1800
ACTIVE={'uploading','stalledUP','forcedUP'}
STOPPED={'stoppedUP','pausedUP'}
STOP_CONFIRMATION_GRACE=1800


def pending_stop_errors(state,now):
    """Allow asynchronous stops two timer intervals, without extending retries.

    A missing/corrupt timestamp cannot grant an indefinite grace period. API
    failures are reported by the caller immediately, independently of this.
    """
    overdue=invalid=0
    for record in state.get('torrents',{}).values():
        if record.get('phase')!='pending':
            continue
        stamp=record.get('intent_at')
        if not isinstance(stamp,(int,float)) or not math.isfinite(stamp) or stamp>now:
            invalid+=1
        elif now-stamp>=STOP_CONFIRMATION_GRACE:
            overdue+=1
    errors=[]
    if invalid:
        errors.append('authorized stop intent timestamp invalid for %d torrent(s); investigation required'%invalid)
    if overdue:
        errors.append('authorized stop unconfirmed after 30 minutes for %d torrent(s); pending retry'%overdue)
    return errors


def initial_retirement():
    return {'version':1,'enabled':False,'waiver':None,'torrents':{}}


def retirement_paused(state):
    """A deliberate pause: disabled with a paused_at stamp, not merely uninitialized.

    Nothing is stopped while paused, so missing credit evidence holds nothing back
    and must not hold the canary's heartbeat down (it masked real failures).
    """
    stamp=(state or {}).get('paused_at')
    return not (state or {}).get('enabled') and isinstance(stamp,(int,float)) and math.isfinite(stamp)


def incarnation(t):
    return {'added_on':t.get('added_on'),'completion_on':t.get('completion_on')}


def waiver_applies(state,t):
    w=state.get('waiver') or {}
    entry=w.get('hashes',{}).get(t['hash'])
    return bool(entry and entry==incarnation(t) and t.get('progress')==1
        and 0<entry['added_on']<=entry['completion_on']<=w['cutoff']
        and w.get('approved_at',0)>=w['cutoff'])


def intentional_stops(state,torrents):
    """Only persisted, proven stop intents authorize an inactive private seed."""
    return {t['hash'] for t in torrents if (state or {}).get('torrents',{}).get(t['hash'],{}).get('phase')
        in ('pending','retired') and state['torrents'][t['hash']].get('incarnation')==incarnation(t)
        and state['torrents'][t['hash']].get('proof')}


def advance_retirement(previous,torrents,records,identities,healthy,scan,now,valid=True):
    s=copy.deepcopy(previous or initial_retirement())
    if s.get('version')!=1 or not isinstance(s.get('torrents'),dict):
        raise ValueError('unsupported retirement state; stopping held')
    actions,errors=[],[]
    for t in torrents:
        h=t['hash']; old=s['torrents'].get(h)
        if old and old.get('incarnation')!=incarnation(t):
            del s['torrents'][h]; old=None
        if old and old.get('phase') in ('pending','retired') and t.get('state') in STOPPED:
            # API acknowledgement is not enough. Observed stopped state confirms
            # an already-persisted authorized intent, even if trackers now idle.
            old.update(phase='retired',retired_at=old.get('retired_at',now))
            continue
        if old and old.get('phase') in ('pending','retired'):
            # HTTP 200 may precede libtorrent's stoppingUP -> stoppedUP
            # transition. Never discard the write-ahead intent during that
            # handoff, on an API outage, or after a crash/restart.
            if t.get('seeding_time',0)<(old.get('counter') or 0):
                errors.append('retirement seeding counter regressed; buffer reset: '+sanitize(t['name']))
                old.update(phase='buffer',healthy_seconds=0,last_observed=None,scans=0,last_scan=None)
                old.pop('intent_at',None)
                continue
            if old['phase']=='retired':
                errors.append('intentionally retired torrent resumed unexpectedly: '+sanitize(t['name']))
            if s.get('enabled') and valid and scan is not None and t.get('state') in ACTIVE \
                    and t.get('progress')==1 and (not private_torrent(t) or h in healthy):
                old['phase']='pending'
                actions.append(h)
            continue
        if not s.get('enabled'):
            continue
        proof=None
        if not private_torrent(t):
            if t.get('progress')==1 and t.get('seeding_time',0)>=3*86400:
                proof='public:3d'
        elif 'IPT' in tags(t) and t.get('progress')==1:
            if waiver_applies(s,t): proof='waiver:'+str(s['waiver']['cutoff'])
            elif eligible(records.get(identities.get(h)),True): proof='earned:'+identities[h]
        if not old or old.get('proof')!=proof:
            old={'incarnation':incarnation(t),'proof':proof,'phase':'buffer',
                 'healthy_seconds':0,'last_observed':None,'counter':None,'scans':0,'last_scan':None}
            s['torrents'][h]=old
        active=t.get('state') in ACTIVE
        good=valid and scan is not None and proof and active and (not private_torrent(t) or h in healthy)
        if not good:
            old.update(healthy_seconds=0,last_observed=None,phase='buffer',scans=0,last_scan=None)
            continue
        counter=t.get('seeding_time',0)
        last=old.get('last_observed')
        if last is not None:
            dt=now-last; dc=counter-old['counter']
            if dc<0:
                errors.append('retirement seeding counter regressed; buffer reset: '+sanitize(t['name']))
            if dt<0 or dt>MAX_OBSERVATION_GAP or dc<0:
                old.update(healthy_seconds=0,scans=0,last_scan=None,phase='buffer')
            else:
                # This is the EXTRA post-obligation buffer, never earned IPT
                # credit. /info local counters can remain cached for idle seeds;
                # require fresh healthy monitoring + an active complete state,
                # not upload traffic or a moving local quota counter.
                old['healthy_seconds']+=dt
        old.update(last_observed=now,counter=counter)
        if scan!=old.get('last_scan'):
            old['scans']+=1;old['last_scan']=scan
        if proof=='public:3d' or old['healthy_seconds']>=BUFFER_SECONDS and old['scans']>=2:
            old.update(phase='pending',intent_at=old.get('intent_at',now))
            actions.append(h)
    return s,actions,errors
