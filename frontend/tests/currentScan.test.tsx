import { render, screen, fireEvent } from '@testing-library/react';
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
 it('shows actual zero scores and skipped coins with local details, without querying nonexistent journal evidence',()=>{
  const {ownerRead}=setup(at+10000);
  expect(screen.getByText(/1 evaluated of 2 shortlisted/)).toBeInTheDocument();
  expect(screen.getByText(/score \+0.000/)).toBeInTheDocument();
  expect(screen.getByText(/ETH · not evaluated: no applicable strategy/)).toBeInTheDocument();
  fireEvent.click(screen.getByRole('button',{name:'BTC decision detail'}));
  expect(screen.getByText(/Full decision evidence is saved only for trade attempts/)).toBeInTheDocument();
  expect(screen.queryByText('Analyst votes')).not.toBeInTheDocument();
  expect(ownerRead).not.toHaveBeenCalled();
 });
 it('expires on browser time even when the last successful poll said fresh',()=>{
  setup(at+181000);
  expect(screen.getByText(/STALE · last completed scan/)).toBeInTheDocument();
 });
});
