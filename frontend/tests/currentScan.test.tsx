import { render, screen, fireEvent, within } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { describe, it, expect, vi } from 'vitest';
import { PreviewProvider } from '../src/context';
import { fixtureAdapter } from '../src/adapters/fixture';
import { SignalsCard } from '../src/views/Overview';

const at=Date.parse('2026-10-25T16:00:00Z');
const desk:any={scan:{scan_id:'42:3',observed_at:new Date(at).toISOString(),freshness:'fresh',shortlisted:2,evaluated:1,traded:0,
 coins:[{decision_id:'d1',symbol:'BTC/USDT',action:'HOLD',score:0,threshold:.24,confidence:0,executed:false,evaluated:true,persisted:false,regime:'RANGING',price:100,strategy_checks:[{strategy:'breakout',reasons:['returned_none']}]},
 {decision_id:'scan:ETH',symbol:'ETH/USDT',action:'NOT_EVALUATED',score:null,threshold:null,confidence:null,executed:false,evaluated:false,persisted:false,skip_reason:'NO_ELIGIBLE_STRATEGY'}]}};
function setup(now:number){
 const ownerRead=vi.fn();const adapter={...fixtureAdapter,mode:'LIVE' as const,ownerRead};
 const client=new QueryClient({defaultOptions:{queries:{retry:false}}});
 const view=render(<QueryClientProvider client={client}><PreviewProvider adapter={adapter as any}><SignalsCard desk={desk} loading={false} now={now}/></PreviewProvider></QueryClientProvider>);
 return {ownerRead,view};
}
describe('current completed scan',()=>{
 it('shows one list with concise no-signal and not-applicable results without fabricated metrics',()=>{
  const {ownerRead}=setup(at+10000);
  expect(screen.getByText(/1 evaluated of 2 shortlisted/)).toBeInTheDocument();
  expect(screen.getByText('No signal')).toBeInTheDocument();
  expect(screen.getByText('Not applicable')).toBeInTheDocument();
  expect(screen.getAllByRole('table')).toHaveLength(1);
  expect(screen.getAllByRole('row')).toHaveLength(3);
  expect(screen.queryByText(/strongest 5|All 2 shortlisted/)).not.toBeInTheDocument();
  const skipped=screen.getByText('ETH').closest('tr')!;
  expect(within(skipped).getAllByText('—')).toHaveLength(3);
  fireEvent.click(screen.getByRole('button',{name:'BTC decision detail'}));
  expect(screen.getByText('Strategy checks')).toBeInTheDocument();
  expect(screen.queryByText('Analyst votes')).not.toBeInTheDocument();
  expect(ownerRead).not.toHaveBeenCalled();
 });
 it('skipped details contain only a short status and no numeric fields',()=>{
  setup(at+10000);
  fireEvent.click(screen.getByRole('button',{name:'ETH decision detail'}));
  const detail=screen.getByRole('dialog');
  expect(within(detail).getByText('Not applicable')).toBeInTheDocument();
  expect(within(detail).queryByText('Score')).not.toBeInTheDocument();
  expect(within(detail).queryByText('Strategy checks')).not.toBeInTheDocument();
 });
 it('expires on browser time even when the last successful poll said fresh',()=>{
  setup(at+181000);
  expect(screen.getByText(/STALE · last completed scan/)).toBeInTheDocument();
 });
});
