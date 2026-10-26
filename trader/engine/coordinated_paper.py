"""Durable two-leg PAPER execution using the research HedgeBook.

No exchange, real trade, control, approval or account writer is reachable.
Orders fill at the following bar's open under declared simulation costs.
An admission/install consumer must separately authorize a book before start.
"""
from dataclasses import asdict
import json
import math

from ..research.closure import canonical, digest
from ..research.compose import hedge as H

TABLE = 'coordinated_paper_books'


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

    def start(self, *, spec, identity, equity=10000.0,primary_exit=None):
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
        ident = dict(spec=spec, identity=identity, starting_equity=equity)
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
            contracts={leg:H.Contract(spec[leg]['contract'],spec[leg]['multiplier']) for leg in ('primary','hedge')}
            closes=dict(primary=observation['p'],hedge=observation['h'])
            opens=dict(primary=observation['po'],hedge=observation['ho'])
            valid=lambda p:isinstance(p,(int,float)) and math.isfinite(p) and p>0
            known=all(valid(p) for p in (*closes.values(),*opens.values()))
            before=dict(book.pos);cost=0.0
            actual={} if fills is None else dict(fills)
            if fills is None:
                actual={oid:qty if known else 0.0 for oid,(leg,side,qty,_) in book.pending.items()}
            # A hold prevents an already queued increase from filling too.
            for oid,(leg,side,qty,_) in book.pending.items():
                if not allow_entry and (book.pos[leg]==0 or book.pos[leg]*side>0):
                    actual[oid]=0.0
            if not known and any(actual.values()):
                raise ValueError('coordinated_paper_fill_price_missing')
            for oid,qty in actual.items():
                if oid not in book.pending:
                    raise ValueError('coordinated_paper_late_or_unknown_fill')
                leg,side,requested,issued=book.pending[oid]
                if issued!=bar-1 or not isinstance(qty,(int,float)) or not math.isfinite(qty) or not 0<=qty<=requested:
                    raise ValueError('coordinated_paper_fill_invalid')
                if qty:
                    cost+=contracts[leg].notional(qty,opens[leg])*(spec['fee_bps']+spec['slip_bps'])/10000
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
            funding=observation.get('funding')
            if funding is not None:
                if not known or set(funding)!={'primary','hedge'} or not all(math.isfinite(v) for v in funding.values()):
                    raise ValueError('coordinated_paper_funding_invalid')
                for leg in ('primary','hedge'):
                    carry=-math.copysign(1,before[leg])*contracts[leg].notional(before[leg],opens[leg])*funding[leg] if before[leg] else 0.0
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
            intents=book.step(bar,dict(p=closes['primary'] if valid(closes['primary']) else float('nan'),
                h=closes['hedge'] if valid(closes['hedge']) else float('nan'),
                signal=observation['signal'] if allow_entry and not liquidated else 0,
                exit=bool(observation.get('exit') or liquidated),
                ratio=observation['ratio'] if observation['ratio'] is not None else float('nan'),corr=observation.get('corr'),
                equity=max(state['equity'],1e-12),prim_c=contracts['primary'],hedge_c=contracts['hedge'],
                primary_exit_observation=observation.get('primary_exit_observation')))
            state.update(bar=bar,book=asdict(book),costs=state['costs']+cost)
            state['book']['log']=[]  # events retain intent history; recovery state stays bounded
            result=dict(book_id=key,bar=bar,request=request,fills=actual,intents=[asdict(x) for x in intents],
                        positions=book.pos,equity=state['equity'] if known else None,
                        costs=cost,held=held,gross_notional=gross,liquidated=liquidated,incomplete_marks=state['incomplete_marks'],
                        fill_basis='SIMULATED_NEXT_BAR_OPEN',cost_basis='DECLARED_FEE_AND_SLIPPAGE_ESTIMATE',
                        funding_basis='SUPPLIED_SIGNED_RATES' if funding is not None else 'UNAVAILABLE',
                        exit_input_gaps=book.exit_input_gaps,
                        net_economics='UNAVAILABLE' if funding is None or state['incomplete_marks'] or state.get('incomplete_funding') or book.exit_input_gaps else 'MODELLED_PAPER_ONLY',
                        real_order_submissions=0)
            db.execute('INSERT INTO coordinated_paper_events VALUES(?,?,?,?)',(key,bar,canonical(result),digest(result)))
            db.execute('UPDATE coordinated_paper_books SET payload=?,sha256=? WHERE book_id=?',(canonical(state),digest(state),key))
        return result
