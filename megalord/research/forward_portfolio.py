"""Coupled prospective quote portfolio using actual Megalord admission rules.

Quote fills remain models. Unknown venue/operating costs never become net PnL.
No broker or network API is imported by this module.
"""
from copy import deepcopy
from dataclasses import asdict

import pandas as pd

from megalord.arbiter.signal_arbiter import MegalordSignalArbiter
from megalord.firewall.risk_firewall import MegalordRiskFirewall
from megalord.research.decision_evidence import capture_entry_context, finish_entry_context
from megalord.research.quote_replay import TwinPosition, Quote


class ForwardPortfolio:
    def __init__(self, config, *, started_at):
        self.config=deepcopy(config)
        self.started_at=pd.Timestamp(started_at)
        if self.started_at.tzinfo is None:raise ValueError('Aware prospective start required')
        self.arbiter=MegalordSignalArbiter(self.config)
        self.firewall=MegalordRiskFirewall(self.config)
        self.positions={};self.closed=[];self.processed=set();self.latest_quotes={}
        self.daily_date=None;self.daily_realized_loss_r=0.
        self.realized_quote_r=0.;self.peak_equity_quote_r=0.;self.max_drawdown_quote_r=0.
        self.events=[]

    def roll_day(self, at):
        day=pd.Timestamp(at).tz_convert('UTC').date()
        if day!=self.daily_date:
            self.daily_date=day;self.daily_realized_loss_r=0.

    def active_positions(self):
        result=[]
        for row in self.positions.values():
            result.append(dict(row['admission'],be_moved=row['model'].be_moved,
                               trailing_level=row['model'].trailing_level))
        return result

    def advance(self, symbol, quote, *, poll):
        quote.validate()
        self.roll_day(quote.time)
        self.latest_quotes[symbol]=quote
        for key,row in list(self.positions.items()):
            if row['admission']['symbol']!=symbol:continue
            model=row['model'];before=len(model.events)
            if pd.Timestamp(quote.time)<model.last_time:continue
            model.advance(quote,poll=poll)
            new=model.events[before:]
            for event in new:
                weighted=event['gross_r']*row['admission']['allocated_risk_r']
                self.realized_quote_r+=weighted
                self.events.append(dict(kind='MODELED_EXIT_LEG',signal_id=key,
                    allocated_quote_r=weighted,broker_fill_verified=False,**event))
            if not any(model.open):
                raw=model.summary()['realized_quote_r']
                weighted=raw*row['admission']['allocated_risk_r']
                self.arbiter.record_trade_outcome(row['admission']['engine_name'],weighted)
                if weighted<0:self.daily_realized_loss_r+=abs(weighted)
                self.closed.append({**row['admission'],**model.summary(),'allocated_quote_r':weighted})
                del self.positions[key]
        self.mark_equity()

    def admit(self, signal, *, at, quote, point, macro_bias, news_check, news_available_at):
        """Caller supplies contemporaneous inputs; all gate decisions are replayed."""
        self.roll_day(at)
        if signal.signal_id in self.processed:return {'status':'ALREADY_PROCESSED'}
        if pd.Timestamp(signal.entry_time)<self.started_at:
            self.processed.add(signal.signal_id)
            return {'status':'BEFORE_PROSPECTIVE_START'}
        if quote is None:return {'status':'MISSING_FRESH_QUOTE_RETRY'}
        age=(pd.Timestamp(at)-pd.Timestamp(quote.time)).total_seconds()
        if age<0 or age>60:return {'status':'STALE_QUOTE_RETRY'}
        if point<=0:raise ValueError('Broker point required')
        quote.validate();candidate=deepcopy(signal)
        candidate.metadata=dict(candidate.metadata or {},quality_context='live',live_active=True,
            current_price=quote.ask if signal.direction=='LONG' else quote.bid,
            spread_price=quote.ask-quote.bid,spread_points=(quote.ask-quote.bid)/point,point=point)
        if macro_bias:candidate.metadata['htf_bias']=macro_bias
        # Same contemporaneous Binance-futures basis normalization as the daemon.
        if candidate.metadata.get('price_source')=='binance_futures':
            sig_age=(pd.Timestamp(at)-pd.Timestamp(candidate.entry_time)).total_seconds()
            if -5<=sig_age<=180:
                shift=candidate.metadata['current_price']-candidate.entry_price
                for name in ('entry_price','sl_price','tp1_price','tp2_price'):
                    value=getattr(candidate,name)
                    if value>0:setattr(candidate,name,round(value+shift,5))
                candidate.metadata['basis_shift_applied']=shift
        active=self.active_positions()
        context=capture_entry_context(self.config,candidate,decided_at=at,active_positions=active,
            daily_realized_loss_r=self.daily_realized_loss_r,loss_streaks=self.arbiter.engine_loss_streaks,
            macro_bias=macro_bias)
        decision=self.arbiter.evaluate_signal(candidate,active,macro_htf_bias=macro_bias,now_utc=at)
        waiting=not decision.allowed and decision.action=='QUALITY_ENTRY_DRIFT' and candidate.metadata.get('status')=='PENDING_LIMIT'
        if not waiting:self.processed.add(signal.signal_id)
        kwargs={}
        status='WAITING_LIMIT_PULLBACK' if waiting else 'ARBITER_REJECTED'
        if decision.allowed:
            if news_check is None or type(news_check.get('is_frozen')) is not bool:
                # Missing news is unknown. Do not approve or consume the signal.
                self.processed.discard(signal.signal_id)
                return {'status':'MISSING_OBSERVED_NEWS_RETRY'}
            kwargs.update(news_check=news_check,news_available_at=news_available_at)
            status='NEWS_REJECTED'
            if not news_check['is_frozen']:
                fw=self.firewall.evaluate_entry(candidate,decision.final_risk_mult,self.daily_realized_loss_r,active)
                kwargs['firewall_result']=fw;status='FIREWALL_REJECTED'
                if fw[0]:status='MODEL_APPROVED'
        evidence=finish_entry_context(context,decision=decision,**kwargs)
        if not evidence['gate_parity_equal']:raise ValueError('Recorded gate replay mismatch')
        result=dict(status=status,signal_id=signal.signal_id,evidence=evidence)
        if status=='MODEL_APPROVED':
            fill_at=max(pd.Timestamp(at),pd.Timestamp(news_available_at))
            model=TwinPosition(engine=candidate.engine_name,symbol=candidate.symbol,
                direction=candidate.direction,quote=Quote(fill_at,quote.bid,quote.ask),stop=candidate.sl_price,
                tp1=candidate.tp1_price,tp2=candidate.tp2_price)
            admission=dict(signal_id=candidate.signal_id,engine_name=candidate.engine_name,
                symbol=candidate.symbol,direction=candidate.direction,math_family=candidate.math_family.value,
                strategy_regime=candidate.strategy_regime.value,allocated_risk_r=decision.final_risk_mult,
                source_entry_time=str(candidate.entry_time),observed_entry_time=str(fill_at),
                broker_fill_verified=False)
            self.positions[candidate.signal_id]=dict(admission=admission,model=model)
            self.latest_quotes[candidate.symbol]=quote
            self.events.append(dict(kind='MODELED_ENTRY',**admission,fill=model.plan.entry,
                stop=model.plan.stop,tp1=model.plan.tp1,tp2=model.plan.tp2))
        return result

    def mark_equity(self):
        unrealized=0.
        for row in self.positions.values():
            model=row['model'];quote=self.latest_quotes.get(model.symbol)
            if quote is None:continue
            mark=quote.bid if model.direction=='LONG' else quote.ask
            unrealized+=.5*sum(model.open)*model.sign*(mark-model.plan.entry)/model.plan.risk_distance*row['admission']['allocated_risk_r']
        equity=self.realized_quote_r+unrealized
        self.peak_equity_quote_r=max(self.peak_equity_quote_r,equity)
        self.max_drawdown_quote_r=max(self.max_drawdown_quote_r,self.peak_equity_quote_r-equity)
        return equity,unrealized

    def summary(self):
        equity,unrealized=self.mark_equity()
        return dict(closed_model_trades=len(self.closed),open_model_trades=len(self.positions),
            realized_weighted_quote_r=self.realized_quote_r,unrealized_weighted_quote_r=unrealized,
            equity_weighted_quote_r=equity,max_drawdown_quote_r=self.max_drawdown_quote_r,
            daily_realized_quote_loss_r=self.daily_realized_loss_r,
            engine_loss_streaks=self.arbiter.engine_loss_streaks,
            complete_venue_net_r=None,complete_operating_net_cash=None,
            quote_portfolio_only=True,net_edge_proven=False,live_approved=False)

    def snapshot(self):
        return dict(summary=self.summary(),closed=self.closed,
            open=[dict(row['admission'],plan=asdict(row['model'].plan),events=row['model'].events,
                stops=row['model'].stops,open_legs=row['model'].open) for row in self.positions.values()])
