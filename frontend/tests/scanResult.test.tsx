import { render, screen, fireEvent, within } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { describe, it, expect, vi } from 'vitest';
import cases from './fixtures/scan-result-cases.json';
import { scanResult, scanClassification } from '../src/scanResult';
import { SignalsCard } from '../src/views/Overview';
import { PreviewProvider } from '../src/context';
import { fixtureAdapter } from '../src/adapters/fixture';

describe('legacy classification compatibility', () => {
  it.each(cases)('$name', test => {
    const coin: any = { action: 'HOLD', score: 0, evaluated: true, executed: false, ...test.coin };
    expect(scanClassification(coin)).toMatchObject({category:test.category,label:test.label,show_metrics:test.show_metrics});
    expect(scanResult(coin)).toBe(test.label);
  });
});

const canonical: any = {schema:'scan-result-classification.v1',category:'future_category',label:'Server-owned result',
  description:'The server supplies the display metadata.',tone:'warning',show_metrics:false,reason_codes:['future_reason']};

it('uses canonical metadata even when legacy action and reasons disagree', () => {
  const coin: any = {action:'BUY',score:1,executed:true,classification:canonical};
  expect(scanClassification(coin)).toBe(canonical);
  expect(scanResult(coin)).toBe('Server-owned result');
});

it.each([
  {...canonical,schema:'unsupported.v2'}, {...canonical,label:''},
  {...canonical,label:null}, {...canonical,category:null}, {...canonical,show_metrics:null},
])('falls back safely on malformed or unsupported metadata %#', classification => {
  expect(scanResult({action:'BUY',score:1,executed:true,classification} as any)).toBe('Order sent');
});

it.each([true, false])('uses the same canonical label in the table and detail panel (evaluated=%s)', evaluated => {
  const coin: any = {decision_id:'d',symbol:'BTC/USDT',action:'HOLD',score:0,threshold:.2,confidence:0,
    executed:false,evaluated,persisted:false,classification:canonical,skip_reason:'future_reason'};
  const ownerRead = vi.fn();
  const adapter = {...fixtureAdapter,mode:'LIVE' as const,ownerRead};
  const client = new QueryClient({defaultOptions:{queries:{retry:false}}});
  const at = '2026-11-01T00:00:00Z';
  const desk: any = {scan:{scan_id:'s',observed_at:at,coins:[coin],traded:0}};
  render(<QueryClientProvider client={client}><PreviewProvider adapter={adapter as any}>
    <SignalsCard desk={desk} loading={false} now={Date.parse(at)}/>
  </PreviewProvider></QueryClientProvider>);
  expect(within(screen.getByRole('table')).getByText('Server-owned result')).toBeInTheDocument();
  expect(within(screen.getByRole('table')).getAllByText('—')).toHaveLength(3);
  fireEvent.click(screen.getByRole('button',{name:'BTC decision detail'}));
  const detail = screen.getByRole('dialog');
  expect(detail).toHaveAccessibleName('BTC-USDT · Server-owned result');
  expect(within(detail).getByText('Server-owned result')).toBeInTheDocument();
  expect(ownerRead).not.toHaveBeenCalled();
});
