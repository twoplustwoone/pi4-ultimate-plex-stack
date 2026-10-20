import copy
import unittest
from torrent_retirement import advance_retirement, initial_retirement, intentional_stops, pending_stop_errors


H='a'*40
def torrent(**changes):
    result=dict(hash=H,name='Example',private=True,tags='IPT, private',state='stalledUP',
                progress=1,added_on=10,completion_on=20,seeding_time=100)
    result.update(changes)
    return result


class RetirementTests(unittest.TestCase):
    def waived(self):
        s=initial_retirement()
        s['enabled']=True
        s['waiver']={'cutoff':30,'source':'https://iptorrents.com/inbox/1015696',
            'approved_at':40,'hashes':{H:{'added_on':10,'completion_on':20}}}
        return s

    def step(self,s,t,now,healthy=True,valid=True,records=None,identities=None):
        return advance_retirement(s,[t],records or {},identities or {},
            {H} if healthy else set(),now,now,valid=valid)

    def ready(self):
        s=self.waived()
        for n in range(97):
            s,actions,errors=self.step(s,torrent(seeding_time=100+n*900),100+n*900)
        self.assertEqual(actions,[H])
        return s

    def test_waiver_requires_full_observed_buffer_not_old_local_counter(self):
        s,actions,_=self.step(self.waived(),torrent(seeding_time=999999),100)
        self.assertEqual(s['torrents'][H]['healthy_seconds'],0)
        self.assertFalse(actions)
        self.ready()

    def test_healthy_zero_peer_seed_does_not_depend_on_local_counter_updates(self):
        s=self.waived()
        for n in range(97):
            s,actions,_=self.step(s,torrent(seeding_time=100),100+n*900)
        self.assertEqual(actions,[H])
        self.assertEqual(s['torrents'][H]['healthy_seconds'],86400)

    def test_failure_and_monitor_gap_reset_buffer(self):
        s,_,_=self.step(self.waived(),torrent(),100)
        s,_,_=self.step(s,torrent(seeding_time=1000),1000)
        self.assertEqual(s['torrents'][H]['healthy_seconds'],900)
        for kw,now in [({'healthy':False},1100),({'valid':False},1100),({},4000)]:
            candidate,actions,_=self.step(s,torrent(seeding_time=4000),now,**kw)
            self.assertFalse(actions)
            self.assertEqual(candidate['torrents'][H]['healthy_seconds'],0)

    def test_unknown_credit_and_new_torrent_never_inherit_waiver(self):
        for t in [torrent(added_on=31),torrent(completion_on=31),
                  torrent(progress=.5,state='downloading')]:
            _,actions,_=self.step(self.waived(),t,100000)
            self.assertFalse(actions)
        _,actions,_=self.step(initial_retirement(),torrent(),100000)
        self.assertFalse(actions)

    def test_new_torrent_can_use_positive_earned_evidence(self):
        s=initial_retirement();s['enabled']=True
        records={'123':{'credited_hours_lower':336.05,'ratio_confirmed':False}}
        for n in range(97):
            s,actions,_=self.step(s,torrent(seeding_time=100+n*900),100+n*900,
                records=records,identities={H:'123'})
        self.assertEqual(actions,[H])
        self.assertEqual(s['torrents'][H]['proof'],'earned:123')

    def test_partial_does_not_retire_even_on_ratio(self):
        s=initial_retirement();s['enabled']=True
        _,actions,_=self.step(s,torrent(progress=.5),100,
            records={'123':{'credited_hours_lower':0,'ratio_confirmed':True}},identities={H:'123'})
        self.assertFalse(actions) # Must finish wanted download before stopping.

    def test_pending_stop_is_durable_and_requires_observed_confirmation(self):
        s=self.ready()
        self.assertEqual(s['torrents'][H]['phase'],'pending')
        self.assertEqual(intentional_stops(s,[torrent(state='stoppedUP')]),{H})
        candidate,actions,_=self.step(s,torrent(state='stoppedUP'),86501,healthy=False)
        self.assertEqual(candidate['torrents'][H]['phase'],'retired')
        self.assertFalse(actions)
        self.assertEqual(s['torrents'][H]['phase'],'pending') # Report candidate is pure.

    def test_async_stop_confirmation_preserves_persisted_intent(self):
        s=self.ready()
        pending,actions,_=self.step(s,torrent(state='stoppingUP',seeding_time=86500),86501,valid=False)
        self.assertEqual(pending['torrents'][H]['phase'],'pending')
        self.assertFalse(actions)
        confirmed,actions,_=self.step(pending,torrent(state='stoppedUP',seeding_time=86500),87400,healthy=False)
        self.assertEqual(confirmed['torrents'][H]['phase'],'retired')
        self.assertFalse(actions)

    def test_failed_stop_request_retries_only_with_fresh_healthy_monitor(self):
        s=self.ready()
        pending,actions,_=self.step(s,torrent(seeding_time=86501),86501,valid=False)
        self.assertEqual(pending['torrents'][H]['phase'],'pending')
        self.assertFalse(actions)
        _,actions,_=self.step(pending,torrent(seeding_time=87400),87400)
        self.assertEqual(actions,[H])

    def test_async_stop_has_bounded_confirmation_grace(self):
        s=self.ready(); intent=s['torrents'][H]['intent_at']
        for elapsed in (1,900,1799):
            s,_,_=self.step(s,torrent(state='stoppingUP',seeding_time=86500),intent+elapsed,valid=False)
            self.assertEqual(pending_stop_errors(s,intent+elapsed),[])
        self.assertTrue(pending_stop_errors(s,intent+1800))
        s,_,_=self.step(s,torrent(state='stoppedUP',seeding_time=86500),intent+1801,valid=False)
        self.assertEqual(pending_stop_errors(s,intent+1801),[])

    def test_stop_retry_does_not_restart_grace_after_reload(self):
        import json
        s=self.ready(); intent=s['torrents'][H]['intent_at']
        s=json.loads(json.dumps(s))
        s,actions,_=self.step(s,torrent(seeding_time=90000),intent+1900)
        self.assertEqual(actions,[H])
        self.assertEqual(s['torrents'][H]['intent_at'],intent)
        self.assertTrue(pending_stop_errors(s,intent+1900))

    def test_missing_invalid_or_future_stop_intent_is_not_silently_held(self):
        for stamp in (None,'bad',float('nan'),float('inf'),999999):
            s=self.ready(); s['torrents'][H]['intent_at']=stamp
            self.assertTrue(pending_stop_errors(s,90000))

    def test_counter_reset_and_readded_hash_cannot_reuse_buffer(self):
        s=self.ready()
        candidate,actions,errors=self.step(s,torrent(seeding_time=0),86501)
        self.assertFalse(actions)
        self.assertTrue(errors)
        self.assertEqual(candidate['torrents'][H]['healthy_seconds'],0)
        self.assertFalse(intentional_stops(s,[torrent(added_on=1000,state='stoppedUP')]))

    def test_public_stop_independent_of_library_and_private_hold(self):
        s=initial_retirement();s['enabled']=True
        t=torrent(private=False,tags='public',seeding_time=3*86400)
        _,actions,_=self.step(s,t,100)
        self.assertEqual(actions,[H])
        _,actions,_=self.step(s,torrent(tags='private, other',seeding_time=999999),100)
        self.assertFalse(actions)

    def test_disabled_policy_and_read_only_input(self):
        s=self.waived();s['enabled']=False; original=copy.deepcopy(s)
        _,actions,_=self.step(s,torrent(),100)
        self.assertFalse(actions)
        self.assertEqual(s,original)

    def test_duplicate_scan_not_enough_for_confirmation(self):
        s=self.waived()
        for n in range(97):
            s,actions,_=advance_retirement(s,[torrent(seeding_time=100+n*900)],{}, {},{H},
                100,100+n*900,valid=True)
        self.assertFalse(actions)


if __name__=='__main__': unittest.main()
