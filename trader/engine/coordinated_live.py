"""Durable shared-model state around the existing coordinated dispatcher.

Model intent, actual fills and venue-flat reconciliation are separate events.
No second broker writer, financial estimator, approval or promotion lives here.
"""
from copy import deepcopy
from dataclasses import asdict,is_dataclass
import json
import math
import time

from . import coordinated_authority as CA,coordinated_venue as V
from . import coordinated_close as C,coordinated_unwind as U
from ..research.closure import canonical,digest
from ..research.compose import hedge as H

TABLE='coordinated_live_books'


class LiveBooks:
    def __init__(self,executor):
        self.executor=executor;self.journal=executor.journal
        with self.journal._tx() as db:
            db.executescript('''CREATE TABLE IF NOT EXISTS coordinated_live_books(
                group_id TEXT PRIMARY KEY,payload TEXT NOT NULL,sha256 TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS coordinated_live_actions(
                group_id TEXT NOT NULL,oid TEXT NOT NULL,payload TEXT NOT NULL,sha256 TEXT NOT NULL,
                PRIMARY KEY(group_id,oid));
                CREATE TRIGGER IF NOT EXISTS coordinated_live_actions_no_update BEFORE UPDATE ON coordinated_live_actions
                BEGIN SELECT RAISE(ABORT,'live action evidence is immutable'); END;
                CREATE TRIGGER IF NOT EXISTS coordinated_live_actions_no_delete BEFORE DELETE ON coordinated_live_actions
                BEGIN SELECT RAISE(ABORT,'live action evidence is immutable'); END;''')

    def load(self,group_id):
        rows=self.journal.query('SELECT * FROM coordinated_live_books WHERE group_id=?',(group_id,))
        if len(rows)!=1:raise ValueError('coordinated_live_book_missing')
        state=json.loads(rows[0]['payload'])
        if (digest(state)!=rows[0]['sha256'] or state['group_id']!=group_id
                or state['exit_semantics_id']!=H.BOOK_EXIT_SEMANTICS_ID):
            raise ValueError('coordinated_live_book_integrity')
        return state

    def _save(self,before,after,event=None):
        with self.journal._tx() as db:
            db.execute('BEGIN IMMEDIATE')
            current=db.execute('SELECT sha256 FROM coordinated_live_books WHERE group_id=?',(before['group_id'],)).fetchone()
            if current is None or current['sha256']!=digest(before):
                raise ValueError('coordinated_live_book_changed')
            if event:
                db.execute('INSERT INTO coordinated_live_actions VALUES(?,?,?,?)',
                    (after['group_id'],event['oid'],canonical(event),digest(event)))
            db.execute('UPDATE coordinated_live_books SET payload=?,sha256=? WHERE group_id=?',
                       (canonical(after),digest(after),after['group_id']))

    def register(self,book,identity,*,prepared_ms,native_observation=None):
        """Persist before dispatch; rechecks existing exact live eligibility."""
        group=identity.get('coordinated') or {};gid=group.get('group_id');vid=identity.get('version_id')
        if (not gid or not vid or group.get('leg')!='primary' or not isinstance(book,H.HedgeBook)
                or type(prepared_ms) is not int or not 0<=int(time.time()*1000)-prepared_ms<=30000):
            raise ValueError('coordinated_live_registration_invalid')
        with self.executor._entry_lock:
            with CA.execution_scope(self.journal,self.executor.cfg,vid,group_id=gid,leg='primary',action_id='register:'+gid) as (v,install):
                if (book.spec!=v['lineage']['research']['multi_leg']['hedge_spec']
                        or book.primary_exit_definition!=v['spec']['exit']
                        or identity.get('spec_hash')!=v['spec_hash'] or identity.get('spec_sha256')!=v['spec_hash']
                        or identity.get('strategy_id')!=v['strategy_id'] or identity.get('install_id')!=install['install_id']
                        or identity.get('exit_semantics_id')!=H.BOOK_EXIT_SEMANTICS_ID):
                    raise ValueError('coordinated_live_registration_differs')
                state=dict(schema='coordinated-live-book.v1',group_id=gid,identity=deepcopy(identity),
                    exit_semantics_id=H.BOOK_EXIT_SEMANTICS_ID,book=asdict(book),prepared_ms=prepared_ms,
                    native_bar=book.last_clock_bar,observed_ms=prepared_ms,status='OPEN',last_result=None)
                if native_observation is not None:
                    if native_observation.get('signal_cut_ms')!=identity.get('signal_cut_ms'):
                        raise ValueError('coordinated_live_native_observation_differs')
                    state['native_observation']=deepcopy(native_observation)
                with self.journal._tx() as db:
                    old=db.execute('SELECT payload,sha256 FROM coordinated_live_books WHERE group_id=?',(gid,)).fetchone()
                    if old:
                        if old['sha256']!=digest(state) or json.loads(old['payload'])!=json.loads(canonical(state)):
                            raise ValueError('coordinated_live_registration_conflict')
                    else:
                        db.execute('INSERT INTO coordinated_live_books VALUES(?,?,?)',(gid,canonical(state),digest(state)))
        return self.load(gid)

    def prepare(self,group_id,native_bar,observation,*,admission=None):
        """One current observation; pending actions must settle before stepping."""
        with self.executor._entry_lock:
            before=self.load(group_id);state=deepcopy(before);book=H.HedgeBook(**state['book'])
            if state['status'] in ('CLOSED','ABANDONED') or book.pending:return state
            at=observation.get('decision_ms');now=int(time.time()*1000)
            if type(at) is not int or not state['observed_ms']<at<=now or now-at>30000:
                raise ValueError('coordinated_live_observation_clock')
            if type(native_bar) is not int or native_bar<state['native_bar']:
                raise ValueError('coordinated_live_native_clock')
            bar=deepcopy(observation)
            if bar.get('ratio') is None:bar['ratio']=float('nan')
            if native_bar==state['native_bar'] and bar.get('signal'):
                raise ValueError('coordinated_live_repeated_native_signal')
            if book.state=='FLAT' and bar.get('signal'):
                from ..attention_admission import verify_receipt
                admitted=verify_receipt(admission) if admission is not None else None
                if (admitted is None or admitted.get('policy_version')!='volume-crypto.v1'
                        or not 0<=at-admitted['source_cut_ms']<=60000
                        or len(admitted['admitted_symbols'])>30
                        or book.spec['primary']['symbol'] not in admitted['admitted_symbols']):
                    bar['signal']=0
            for leg,key in [('primary','prim_c'),('hedge','hedge_c')]:
                bar[key]=H.Contract(book.spec[leg]['contract'],book.spec[leg]['multiplier'])
            # This is the same sampled-price path as prospective paper, but
            # every quantity change below must come from a real receipt.
            bar['paper_quote']=True
            if state.pop('hedge_reentry_cooldown_pending',False):
                # The stop receipt has no strategy-bar index. Start the
                # declared cooldown at the next verified native observation.
                book.last_rebalance=native_bar
            book.step(native_bar,bar)
            pending=set(book.pending)
            book.log=[x for x in book.log if (asdict(x) if is_dataclass(x) else x)['oid'] in pending][-8:]
            if len(book.pending)>8:raise ValueError('coordinated_live_pending_bound')
            state.update(book=asdict(book),native_bar=native_bar,observed_ms=at,prepared_ms=at,last_result=None)
            if observation.get('native_observation') is not None:
                state['native_observation']=deepcopy(observation['native_observation'])
            self._save(before,state)
            return self.load(group_id)

    def _reconcile_closed(self,before):
        gid=before['group_id']
        rows=(self.journal.query('SELECT * FROM coordinated_group_unwinds WHERE group_id=?',(gid,))
              if U._exists(self.journal) else [])
        if not rows:
            closed=self.journal.query("SELECT * FROM trades WHERE json_extract("
                "CASE WHEN json_valid(entry_identity_json) THEN entry_identity_json ELSE '{}' END,"
                "'$.coordinated.group_id')=? ORDER BY id LIMIT 65",(gid,))
            if len(closed)>64:raise ValueError('coordinated_live_group_bound')
            if not closed:return None
            if any(row['status']!='closed' for row in closed):return self._reconcile_hedge_closed(before,closed)
            expected=before['identity']
            if any(any(json.loads(row['entry_identity_json']).get(k)!=expected.get(k)
                       for k in ('version_id','spec_hash','install_id')) for row in closed):
                raise ValueError('coordinated_live_closed_version_differs')
            # Native protection can close the only leg without an existing
            # unwind. Journal closure alone is not venue-flat proof.
            result=U.request_close(self.executor,closed[0],'owned_group_closed')
            if result['status']!='CLOSED':return result
            rows=self.journal.query('SELECT * FROM coordinated_group_unwinds WHERE group_id=?',(gid,))
        if not rows or rows[0]['state']!='CLOSED':return None
        plan=U._plan(rows[0]);members=U._members(self.executor,gid)
        if members!=plan['members']:raise ValueError('coordinated_live_closed_members_differ')
        for member in members:
            root=json.loads(self.journal.query('SELECT entry_identity_json FROM trades WHERE id=?',
                                               (member['trade_id'],))[0]['entry_identity_json'])
            if any(root.get(k)!=before['identity'].get(k) for k in ('version_id','spec_hash','install_id')):
                raise ValueError('coordinated_live_closed_version_differs')
        if any(self.journal.query('SELECT status FROM trades WHERE id=?',(m['trade_id'],))[0]['status']!='closed' for m in members):
            raise ValueError('coordinated_live_closed_group_has_open_member')
        after=deepcopy(before);after['status']='CLOSED'
        after['book']['pos']={'primary':0.,'hedge':0.};after['book']['pending']={}
        event=dict(oid='reconciled-group-close',kind='OWNED_GROUP_CLOSE_RECONCILIATION',
            unwind_plan_sha256=rows[0]['plan_sha256'],fill_claim=False,financial_result=None)
        self._save(before,after,event)
        return dict(status='CLOSED',group_id=gid,reconciliation=event)

    def _reconcile_hedge_closed(self,before,rows):
        """Adopt owned hedge absence, never a synthetic close fill or PnL.

        Submitted model actions must first recover their original receipt.
        A later open hedge supersedes historical closures. Fresh complete
        account evidence, rather than a closed journal row, proves absence.
        """
        if not before['book']['pos']['hedge']:return None
        gid=before['group_id']
        with self.executor._entry_lock:
            if self.load(gid)!=before:raise ValueError('coordinated_live_book_changed')
            members=U._members(self.executor,gid)
            primary=[r for r in rows if C.ownership(self.executor,r,gid)['leg']=='primary' and r['status']=='open']
            hedges=[r for r in rows if C.ownership(self.executor,r,gid)['leg']=='hedge']
            if len(primary)!=1 or not hedges or any(r['status']=='open' for r in hedges):return None
            from ..research.compose import managed_hedge as M
            policy=before['book']['spec'].get('management') or {}
            if policy.get('version') not in (M.CURRENT_VERSION,M.PAIR_VERSION):return None
            M.validate(policy)
            for row in rows:
                root=json.loads(row['entry_identity_json'])
                if any(root.get(k)!=before['identity'].get(k) for k in ('version_id','spec_hash','install_id')):
                    raise ValueError('coordinated_live_closed_version_differs')
                if root['coordinated'].get('management_policy')!=before['book']['spec'].get('management'):
                    raise ValueError('coordinated_live_closed_management_differs')
            response=U._hedge_closure_response(self.executor,primary[0],gid)
            if response is not None:return response
            # Even terminal-but-unadopted submissions must be consumed by
            # drain first; otherwise their later adoption resurrects a hedge.
            for oid in before['book']['pending']:
                aid='cg_'+V.A.digest([gid,before['identity']['version_id'],oid])
                for table in ('execution_requests','coordinated_reductions'):
                    if (self.journal.query("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",(table,))
                            and self.journal.query('SELECT 1 FROM '+table+' WHERE logical_id=?',(aid,))):
                        return None
            unresolved=self.journal.query("SELECT 1 FROM execution_requests WHERE state!='TERMINAL' AND "
                "json_extract(intent_json,'$.strategy_identity.coordinated.group_id')=? LIMIT 1",(gid,))
            if unresolved:raise ValueError('coordinated_live_hedge_closure_action_unresolved')
            if (self.journal.query("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",(C.TABLE,))
                    and self.journal.query("SELECT 1 FROM coordinated_reductions WHERE group_id=? "
                                           "AND state NOT IN ('BOOKED','REFUSED') LIMIT 1",(gid,))):
                raise ValueError('coordinated_live_hedge_closure_reduction_unresolved')
            obs=U._position_observation(self.executor)
            positions={p.instrument_id.value:p for p in obs.positions if p.position_present}
            hm=[m for m in members if m['leg']=='hedge']
            if any(m['instrument_id'] in positions for m in hm):
                raise ValueError('coordinated_live_closed_hedge_has_venue_residual')
            pm=next(m for m in members if m['trade_id']==primary[0]['id'])
            actual=positions.get(pm['instrument_id']);qty=float(primary[0]['amount'])
            if (actual is None or actual.side!=primary[0]['side']
                    or not math.isclose(actual.absolute_quantity,qty,rel_tol=1e-9,abs_tol=1e-12)
                    or (before['book']['pos']['primary']>0)!=(actual.side=='long')
                    or not math.isclose(abs(before['book']['pos']['primary']),qty,rel_tol=1e-9,abs_tol=1e-12)):
                raise ValueError('coordinated_live_primary_quantity_differs')
            after=deepcopy(before);book=after['book']
            dropped=[oid for oid,p in book['pending'].items() if p[0]=='hedge']
            book['pending']={oid:p for oid,p in book['pending'].items() if oid not in dropped}
            book['log']=[p for p in book['log'] if p['oid'] in book['pending']]
            book.update(pos=dict(book['pos'],hedge=0.),hedge_exit_state=None,hedge_quote_range=None,
                        management_activated=False,last_rebalance=before['native_bar'])
            if book['state']!='UNWINDING':book['state']='PRIMARY_OPEN'
            event=dict(oid='reconciled-hedge-close:'+digest(sorted(r['id'] for r in hedges)),
                kind='OWNED_HEDGE_CLOSE_RECONCILIATION',members=hm,discarded_unsubmitted_intents=dropped,
                observation=dict(observation_id=obs.observation_id,source=obs.source,source_ref=obs.source_ref,
                    environment=obs.environment,request_start_ms=obs.request_start_ms,
                    response_received_ms=obs.response_received_ms,complete=obs.complete,
                    owned_positions=[dict(instrument_id=i,position_present=i in positions,
                        side=positions[i].side if i in positions else None,
                        absolute_quantity=positions[i].absolute_quantity if i in positions else 0.)
                        for i in sorted({m['instrument_id'] for m in members})]),
                fill_claim=False,financial_result=None)
            from ..data.feed import execution_account_scope
            event['observation']['account_scope']=execution_account_scope(self.executor.ex)
            after['last_result']=dict(status='HEDGE_CLOSED_RECONCILED',group_id=gid)
            after['hedge_reentry_cooldown_pending']=True
            self._save(before,after,event)
            return dict(after['last_result'],reconciliation=event)

    def drain(self,group_id,*,references,atrs):
        """One pending action per tick; adopt only an exact terminal quantity."""
        with self.executor._entry_lock:
            before=self.load(group_id)
            if before['status'] in ('CLOSED','ABANDONED'):return dict(group_id=group_id,status=before['status'])
            reconciled=self._reconcile_closed(before)
            if reconciled is not None:return reconciled
            state=deepcopy(before);book=H.HedgeBook(**state['book'])
            if not book.pending:return dict(group_id=group_id,status='IDLE')
            logs=[asdict(x) if is_dataclass(x) else x for x in book.log]
            pending=[x for x in logs if x['oid'] in book.pending]
            if len(pending)!=len(book.pending):raise ValueError('coordinated_live_pending_log_differs')
            ins=H.Instruction(**pending[0]);identity=deepcopy(state['identity'])
            action_id='cg_'+V.A.digest([group_id,identity['version_id'],ins.oid])
            increasing=book.pos[ins.leg]*ins.side>=0 and ins.kind not in ('UNWIND','REDUCE_PRIMARY')
            if increasing and int(time.time()*1000)-state['prepared_ms']>H.MAX_QUOTE_INCREASE_AGE_MS:
                submitted=self.journal.query("SELECT 1 FROM sqlite_master WHERE type='table' AND name='execution_requests'")
                if not submitted or not self.journal.query('SELECT 1 FROM execution_requests WHERE logical_id=?',(action_id,)):
                    other={k:v for k,v in book.pending.items() if k!=ins.oid}
                    book.apply_fills({ins.oid:0.});book.pending.update(other)
                    state['book']=asdict(book)
                    if not book.pending and not any(book.pos.values()):state['status']='ABANDONED'
                    result=dict(action_id=action_id,status='UNSUBMITTED_INTENT_EXPIRED',filled_quantity=0.)
                    state['last_result']=result
                    self._save(before,state,dict(oid=ins.oid,kind='UNSUBMITTED_INTENT_EXPIRED',
                        instruction=asdict(ins),execution=result,financial_result=None))
                    return result
            rows=self.journal.query("SELECT * FROM trades WHERE status='open' AND json_extract("
                "CASE WHEN json_valid(entry_identity_json) THEN entry_identity_json ELSE '{}' END,"
                "'$.coordinated.group_id')=? AND json_extract(entry_identity_json,'$.coordinated.leg')=?",(group_id,ins.leg))
            if len(rows)>1:raise ValueError('coordinated_multiple_open_leg_rows')
            if rows:
                C.ownership(self.executor,rows[0],group_id)
                identity=json.loads(rows[0]['entry_identity_json'])
            else:
                identity['coordinated']['leg']=ins.leg
                identity['exit_state']=book.primary_exit_state
                if ins.leg=='hedge':identity['hedge_exit_state']=book.hedge_exit_state
            identity['coordinated_instruction_observed_ms']=state['prepared_ms']
            result=V.dispatch(self.executor,book,ins,identity=identity,reference=references.get(ins.leg),atr=atrs.get(ins.leg))
            filled=result.get('filled_quantity')
            if filled is not None:
                other={k:v for k,v in book.pending.items() if k!=ins.oid}
                book.apply_fills({ins.oid:filled});book.pending.update(other)
                book.log=[x for x in book.log if (asdict(x) if is_dataclass(x) else x)['oid'] in book.pending]
                state['book']=asdict(book);state['last_result']=result
                if (ins.kind=='ENTER' and ins.leg=='primary' and not any(before['book']['pos'].values())
                        and not book.pending and not any(book.pos.values())):state['status']='ABANDONED'
                self._save(before,state,dict(oid=ins.oid,kind='INTENT_REFUSED' if result['status']=='INTENT_REFUSED' else 'ACTUAL_ACTION_ADOPTED',instruction=asdict(ins),
                    execution=result,book_after=asdict(book),financial_result=None))
            elif state.get('last_result')!=result:
                state['last_result']=result;self._save(before,state)
            return result


def run_once(executor,feed,management_results):
    """Resume registered live books on the existing position worker only.

    This function does not discover, approve, allocate or start a new book.
    Native strategy inputs are calculated once per cut; subsequent ticks use
    current public quotes and the existing owned-cashflow valuation producer.
    """
    journal=executor.journal
    if not journal.query("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",(TABLE,)):
        return []
    rows=journal.query("SELECT group_id FROM coordinated_live_books WHERE json_extract(payload,'$.status')='OPEN' ORDER BY group_id LIMIT 3")
    if len(rows)>2:raise ValueError('coordinated_live_active_book_bound')
    if not rows:return []
    from . import coordinated_trials as inputs
    from ..strategy import factory_handoff as F,exit_policy as E
    live=LiveBooks(executor);marks={r['group_id']:r for r in management_results};out=[];quotes={}
    def quote(symbol,contract):
        key=(symbol,contract)
        if key not in quotes:quotes[key]=inputs.quote(feed,symbol,contract)
        value=quotes[key];now=int(time.time()*1000)
        if (value.get('symbol')!=symbol or value.get('contract')!=contract or not value.get('instrument_id') or not value.get('source')
                or not 0<=now-value['started_ms']<=30000 or not value['started_ms']<=value['received_ms']<=now
                or not all(math.isfinite(float(value[k])) for k in ('bid','ask'))
                or not 0<float(value['bid'])<=float(value['ask'])):
            raise ValueError('coordinated_live_quote_unavailable')
        return value
    for row in rows:
        gid=row['group_id']
        try:
            state=live.load(gid)
            if U.active(journal,gid):
                out.append(dict(group_id=gid,status='UNWINDING'));continue
            closed=live._reconcile_closed(state)
            if closed is not None:out.append(closed);continue
            if not state['book']['pending']:
                marked=marks.get(gid) or {}
                equity=marked.get('marked_equity_usdt')
                if equity is None:raise ValueError('coordinated_live_combined_valuation_unavailable')
                version=F.load_version(journal,state['identity']['version_id'])
                install=F.verify_install(journal,version,current=False)
                raw=inputs.prospective_observation(feed,journal,version,{'install':install},
                    dict(spec=state['book']['spec'],native_observation=state.get('native_observation'),
                         decision_ms=state['observed_ms']),int(time.time()*1000),
                    quote_reader=lambda _feed,symbol,contract:quote(symbol,contract))
                # Estimated paper funding is not live PnL. The existing marked
                # producer supplies actual owned fees and signed cashflows.
                raw['equity']=equity
                raw['signal']=0  # a registered economic position never starts a new trade ID here
                state=live.prepare(gid,raw['native_bar'],raw)
            book=H.HedgeBook(**state['book']);logs=[asdict(x) if is_dataclass(x) else x for x in book.log]
            pending=[x for x in logs if x['oid'] in book.pending]
            references={};atrs={}
            if pending:
                intent=pending[0];leg=intent['leg'];hs=book.spec[leg]
                increasing=(book.pos[leg]*intent['side']>=0 and intent['kind'] not in ('UNWIND','REDUCE_PRIMARY'))
                aid='cg_'+V.A.digest([gid,state['identity']['version_id'],intent['oid']])
                existing=journal.query('SELECT 1 FROM execution_requests WHERE logical_id=?',(aid,))
                expired=int(time.time()*1000)-state['prepared_ms']>H.MAX_QUOTE_INCREASE_AGE_MS
                if increasing and not existing and not expired:
                    q=quote(hs['symbol'],hs['contract'])
                    references[leg]=dict(price=q['ask'] if intent['side']>0 else q['bid'],observed_ms=q['received_ms'],
                        basis='current_public_executable_quote',instrument_id=q['instrument_id'],source=q['source'])
                    saved=book.primary_exit_state if leg=='primary' else book.hedge_exit_state
                    if not saved:raise ValueError('coordinated_live_stop_state_unavailable')
                    atrs[leg]=E.decode(saved)[0].entry_atr
            result=live.drain(gid,references=references,atrs=atrs)
            # Keep status bounded; original receipts remain in their execution
            # and model ledgers, rather than duplicating private payloads here.
            out.append(dict(group_id=gid,status=result['status'],action_id=result.get('action_id'),
                            filled_quantity=result.get('filled_quantity')))
        except Exception as exc:
            out.append(U._unavailable(gid,exc))
    return out
