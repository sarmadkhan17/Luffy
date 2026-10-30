"""Durable two-leg PAPER execution using the research HedgeBook.

No exchange, real trade, control, approval or account writer is reachable.
Legacy books simulate next-open fills. New books use subsequent public quotes
under declared simulation costs, never historical fills before a decision.
An admission/install consumer must separately authorize a book before start.
"""
from dataclasses import asdict
import json
import math

from ..research.closure import canonical, digest
from ..research.compose import hedge as H

TABLE = 'coordinated_paper_books'
QUOTE_TIMING = 'PROSPECTIVE_QUOTE_V1'
BAR_TIMING = 'NEXT_BAR_OPEN'
QUOTE_PROTOCOL = dict(version=QUOTE_TIMING, signal='latest eligible completed native candle',
    fill='first fresh bid/ask response strictly after prior decision; declared costs; simulated quantity',
    mark='current bid/ask midpoint', funding='signed retained rate prorated over elapsed time; estimate',
    clock='quote events for fills/marks; native candle index for strategy waits',
    main_exit='close whole group; stop/target at received quotes; trail/time on native boundaries using post-fill sampled prices',
    maximum_pending_increase_age_ms=H.MAX_QUOTE_INCREASE_AGE_MS)
QUOTE_PROTOCOL_ID = digest(QUOTE_PROTOCOL)


class CoordinatedPaper:
    def __init__(self, journal):
        self.journal = journal
        with journal._tx() as db:
            db.executescript('''
                CREATE TABLE IF NOT EXISTS coordinated_paper_books(
                    book_id TEXT PRIMARY KEY, payload TEXT NOT NULL, sha256 TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS coordinated_paper_events(
                    book_id TEXT NOT NULL, bar INTEGER NOT NULL,
                    payload TEXT NOT NULL, sha256 TEXT NOT NULL, PRIMARY KEY(book_id,bar));
                CREATE TRIGGER IF NOT EXISTS coordinated_paper_events_no_update BEFORE UPDATE ON coordinated_paper_events
                    BEGIN SELECT RAISE(ABORT,'paper execution event is immutable'); END;
                CREATE TRIGGER IF NOT EXISTS coordinated_paper_events_no_delete BEFORE DELETE ON coordinated_paper_events
                    BEGIN SELECT RAISE(ABORT,'paper execution event is immutable'); END;
            ''')

    def start(self, *, spec, identity, equity=10000.0,primary_exit=None,timing=BAR_TIMING):
        """Record an already authorized paper identity, never a venue order.

        The consumer binds version/install/qualification. This engine cannot
        confer that authority; identity is immutable and checked on every bar.
        """
        spec = H.validate(spec)
        if (identity.get('exec_mode') != 'paper' or not all(identity.get(k) for k in
                ('version_id','spec_hash','install_id','admission_id'))):
            raise ValueError('coordinated_paper_identity_required')
        if not math.isfinite(equity) or equity <= 0:
            raise ValueError('coordinated_paper_equity_invalid')
        if timing not in (BAR_TIMING,QUOTE_TIMING):
            raise ValueError('coordinated_paper_timing_unsupported')
        ident = dict(spec=spec, identity=identity, starting_equity=equity)
        if H.capital_enabled(spec):ident['capital_policy_id']=H.K.POLICY_ID
        if timing==QUOTE_TIMING:
            ident.update(paper_execution_protocol=QUOTE_PROTOCOL_ID)
        if primary_exit is not None:ident['primary_exit']=primary_exit
        key = digest(ident)
        state = dict(**ident, book_id=key, book=asdict(H.HedgeBook(spec,primary_exit_definition=primary_exit)), bar=-1,
                     equity=equity, last_prices=None, costs=0.0, incomplete_marks=False,incomplete_funding=False,liquidated=False)
        with self.journal._tx() as db:
            db.execute('INSERT OR IGNORE INTO coordinated_paper_books VALUES(?,?,?)',
                       (key,canonical(state),digest(state)))
        return key

    def state(self, key):
        rows = self.journal.query('SELECT payload,sha256 FROM coordinated_paper_books WHERE book_id=?',(key,))
        if len(rows)!=1:
            raise ValueError('coordinated_paper_book_missing')
        result=json.loads(rows[0]['payload'])
        if digest(result)!=rows[0]['sha256'] or result['book_id']!=key:
            raise ValueError('coordinated_paper_book_corrupt')
        return result

    def advance(self, key, bar, observation, *, fills=None, allow_entry=False):
        """Commit fills, both-leg marks, decisions and recovery state together.

        observation has p/h closes, po/ho opens, signal, exit, ratio, corr and
        per-leg signed funding rates. A fill override exercises IOC failures
        in paper; it cannot address an exchange. Late/unknown fills refuse.
        Repeating the same cut is idempotent; changing it is a conflict.
        """
        request=dict(observation=observation,fills=fills,allow_entry=bool(allow_entry))
        with self.journal._tx() as db:
            db.execute('BEGIN IMMEDIATE')
            old=db.execute('SELECT payload FROM coordinated_paper_events WHERE book_id=? AND bar=?',(key,bar)).fetchone()
            if old:
                result=json.loads(old[0])
                if result['request']!=request:
                    raise ValueError('coordinated_paper_cut_conflict')
                return result
            state=self.state(key)
            if type(bar) is not int or bar!=state['bar']+1:
                raise ValueError('coordinated_paper_clock_gap')
            spec=state['spec'];book=H.HedgeBook(**state['book'])
            if H.capital_enabled(spec) and state.get('capital_policy_id')!=H.K.POLICY_ID:
                raise ValueError('coordinated_paper_capital_protocol_differs')
            quote_mode=state.get('paper_execution_protocol')==QUOTE_PROTOCOL_ID
            if bool(observation.get('paper_quote'))!=quote_mode:
                raise ValueError('coordinated_paper_execution_protocol_differs')
            native_bar=observation['native_bar'] if quote_mode else bar
            decision_ms=observation.get('decision_ms')
            expired_increase=False
            if quote_mode:
                if type(native_bar) is not int or native_bar<0 or native_bar<state.get('native_bar',-1):
                    raise ValueError('coordinated_paper_native_clock_invalid')
                if type(decision_ms) is not int or decision_ms<=state.get('decision_ms',-1):
                    raise ValueError('coordinated_paper_quote_clock_invalid')
                if (type(observation.get('signal_cut_ms')) is not int
                        or observation['signal_cut_ms']>decision_ms):
                    raise ValueError('coordinated_paper_future_signal')
                if native_bar==state.get('native_bar') and (observation['signal'] or observation.get('exit')):
                    raise ValueError('coordinated_paper_signal_repeated_within_native_bar')
                expired_increase=decision_ms-state.get('decision_ms',decision_ms)>H.MAX_QUOTE_INCREASE_AGE_MS
            contracts={leg:H.Contract(spec[leg]['contract'],spec[leg]['multiplier']) for leg in ('primary','hedge')}
            closes=dict(primary=observation['p'],hedge=observation['h'])
            opens=dict(primary=observation['po'],hedge=observation['ho'])
            valid=lambda p:isinstance(p,(int,float)) and not isinstance(p,bool) and math.isfinite(p) and p>0
            known=all(valid(p) for p in (*closes.values(),*opens.values()))
            before=dict(book.pos);cost=0.0
            if quote_mode:
                quotes=observation['quotes']
                if quotes['primary']['source']!=quotes['hedge']['source']:
                    raise ValueError('coordinated_paper_quote_sources_differ')
                for leg in ('primary','hedge'):
                    q=quotes[leg]
                    if (q['symbol']!=spec[leg]['symbol'] or q.get('contract')!=spec[leg]['contract']
                            or type(q['started_ms']) is not int or type(q['received_ms']) is not int
                            or not 0<=q['started_ms']<=q['received_ms']<=decision_ms
                            or decision_ms-q['started_ms']>30_000
                            or q['started_ms']<=state.get('decision_ms',-1)
                            or not q.get('instrument_id') or not q.get('source')
                            or not valid(q['bid']) or not valid(q['ask']) or q['bid']>q['ask']):
                        raise ValueError('coordinated_paper_quote_unavailable_or_stale')
                    if not math.isclose(closes[leg],(q['bid']+q['ask'])/2.,rel_tol=1e-12):
                        raise ValueError('coordinated_paper_quote_mark_differs')
                # A missed management interval cannot become a claim that the
                # full intervening price path was observed.
                if any(before.values()) and decision_ms-state.get('decision_ms',decision_ms)>60_000:
                    state['incomplete_marks']=True
                for leg,side,qty,_ in book.pending.values():
                    opens[leg]=quotes[leg]['ask' if side>0 else 'bid']
            actual={} if fills is None else dict(fills)
            if fills is None:
                actual={oid:qty if known or (H.capital_enabled(spec) and book.pos[leg]*side<0 and valid(opens[leg]))
                        else 0.0 for oid,(leg,side,qty,_) in book.pending.items()}
            # A hold prevents an already queued increase from filling too.
            for oid,(leg,side,qty,_) in book.pending.items():
                if (not allow_entry or expired_increase) and (book.pos[leg]==0 or book.pos[leg]*side>0):
                    actual[oid]=0.0
            if not known and any(qty and not (H.capital_enabled(spec) and oid in book.pending
                    and book.pos[book.pending[oid][0]]*book.pending[oid][1]<0
                    and valid(opens[book.pending[oid][0]])) for oid,qty in actual.items()):
                raise ValueError('coordinated_paper_fill_price_missing')
            funding=observation.get('funding')
            carry=0.
            if funding is not None:
                if not known or set(funding)!={'primary','hedge'} or not all(math.isfinite(v) for v in funding.values()):
                    raise ValueError('coordinated_paper_funding_invalid')
                carry=sum(-math.copysign(1,before[leg])*contracts[leg].notional(before[leg],opens[leg])*funding[leg]
                          for leg in ('primary','hedge') if before[leg])
            gap=(sum(contracts[leg].pnl_usd(before[leg],(state['last_prices'] or {}).get(leg,opens[leg]),opens[leg])
                     for leg in ('primary','hedge')) if known else 0.)
            filling=dict(before)
            for oid,qty in actual.items():
                if oid not in book.pending:
                    raise ValueError('coordinated_paper_late_or_unknown_fill')
                leg,side,requested,issued=book.pending[oid]
                clock_ok=(issued<=native_bar if quote_mode else issued==bar-1)
                if not clock_ok or not isinstance(qty,(int,float)) or not math.isfinite(qty) or not 0<=qty<=requested:
                    raise ValueError('coordinated_paper_fill_invalid')
                if qty:
                    if (H.capital_enabled(spec) and before[leg]*side>=0 and (not known or state['incomplete_marks']
                            or state.get('incomplete_funding') or funding is None and any(before.values()))):
                        qty=0.  # unknown carry is not additional buying power
                    else:
                        qty=H.affordable_fill(spec,filling,opens,state['equity']+gap+carry-cost,leg,side,qty,
                                              (spec['fee_bps']+spec['slip_bps'])/10000)
                    actual[oid]=qty
                    cost+=contracts[leg].notional(qty,opens[leg])*(spec['fee_bps']+spec['slip_bps'])/10000
                    filling[leg]+=side*qty
            book.apply_fills(actual)
            pnl=0.0
            if known:
                for leg in ('primary','hedge'):
                    previous=(state['last_prices'] or {}).get(leg,opens[leg])
                    pnl+=contracts[leg].pnl_usd(before[leg],previous,opens[leg])
                    pnl+=contracts[leg].pnl_usd(book.pos[leg],opens[leg],closes[leg])
                state['equity']+=pnl-cost
                state['last_prices']=closes
            elif any(before.values()):
                state['incomplete_marks']=True
            if funding is not None:
                state['equity']+=carry
            if funding is None and (any(before.values()) or any(book.pos.values())):
                state['incomplete_funding']=True
            held=any(book.pos.values())
            gross=sum(contracts[leg].notional(book.pos[leg],closes[leg]) for leg in ('primary','hedge')) if known else None
            liquidated=bool(state.get('liquidated') or state['equity']<=0 or
                            (gross is not None and gross>0 and state['equity']<spec['maint_margin']*gross))
            state['liquidated']=liquidated
            # Missing marks stay unknown. HedgeBook still sees missing bars and
            # emits the declared unwind, rather than treating missing as zero.
            intents=book.step(native_bar,dict(p=closes['primary'] if valid(closes['primary']) else float('nan'),
                h=closes['hedge'] if valid(closes['hedge']) else float('nan'),
                signal=observation['signal'] if allow_entry and not liquidated else 0,
                exit=bool(observation.get('exit') or liquidated),
                ratio=observation['ratio'] if observation['ratio'] is not None else float('nan'),corr=observation.get('corr'),
                equity=max(state['equity'],1e-12),prim_c=contracts['primary'],hedge_c=contracts['hedge'],
                primary_exit_observation=observation.get('primary_exit_observation'),paper_quote=quote_mode))
            state.update(bar=bar,book=asdict(book),costs=state['costs']+cost)
            if quote_mode:
                state.update(native_bar=native_bar,decision_ms=decision_ms,
                             native_observation=observation['native_observation'])
            state['book']['log']=[]  # events retain intent history; recovery state stays bounded
            result=dict(book_id=key,bar=bar,request=request,fills=actual,intents=[asdict(x) for x in intents],
                        capital_policy_id=state.get('capital_policy_id'),
                        positions=book.pos,equity=state['equity'] if known else None,
                        costs=cost,held=held,gross_notional=gross,liquidated=liquidated,incomplete_marks=state['incomplete_marks'],
                        fill_basis='SIMULATED_FIRST_SUBSEQUENT_BID_ASK' if quote_mode else 'SIMULATED_NEXT_BAR_OPEN',
                        cost_basis='DECLARED_FEE_AND_SLIPPAGE_ESTIMATE',
                        funding_basis='SUPPLIED_SIGNED_RATES' if funding is not None else 'UNAVAILABLE',
                        exit_input_gaps=book.exit_input_gaps,
                        net_economics='UNAVAILABLE' if funding is None or state['incomplete_marks'] or state.get('incomplete_funding') or book.exit_input_gaps else 'MODELLED_PAPER_ONLY',
                        real_order_submissions=0)
            if quote_mode:
                result.update(native_bar=native_bar,decision_ms=decision_ms,paper_execution_protocol=QUOTE_PROTOCOL_ID,
                              funding_basis='RETAINED_SIGNED_RATE_ELAPSED_TIME_ESTIMATE',
                              quantity_basis='SIMULATED_FULL_REQUEST_NOT_VENUE_CAPACITY')
            db.execute('INSERT INTO coordinated_paper_events VALUES(?,?,?,?)',(key,bar,canonical(result),digest(result)))
            db.execute('UPDATE coordinated_paper_books SET payload=?,sha256=? WHERE book_id=?',(canonical(state),digest(state),key))
        return result
