import { render, screen, fireEvent } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { describe, expect, it, vi } from "vitest";
import { PreviewProvider } from "../src/context";
import { ResearchStudies } from "../src/components/ResearchStudies";
import { ResearchData, researchReason } from "../src/components/ResearchEvidence";
import type { OwnerAdapter } from "../src/adapters/contracts";

describe("readable research evidence", () => {
  it("explains serialized missing-input reasons without dumping their JSON", () => {
    const reason='required context absent: [{"reason":"1000PEPE/USDT: retrospective dependency missing trustworthy retained source: derivative:1000PEPE/USDT:funding","requirement":"funding"}]';
    expect(researchReason(reason)).toBe("Trusted historical funding data is missing for 1000PEPE/USDT.");
    expect(researchReason('intake_worker_failed: checkpoint_conflict /root/file.py')).toBe("Research intake could not verify its journal source binding.");
  });
  it("renders precise values and missing values as fields, without raw JSON", () => {
    render(<ResearchData data={{ threshold:0.000000123456789, metric:null, inputs:["funding","ohlcv"] }} />);
    expect(screen.getByText(String(0.000000123456789))).toBeVisible();
    expect(screen.getByText("Not recorded")).toBeVisible();
    expect(screen.getByText("funding")).toBeVisible();
    expect(document.querySelector("pre")).toBeNull();
  });
  it("bounds large tables and lets the owner reveal additional rows", () => {
    render(<ResearchData data={Array.from({length:45},(_,i)=>({symbol:`COIN${i}`,value:i}))} />);
    expect(screen.queryByText("COIN20")).toBeNull();
    fireEvent.click(screen.getByRole("button",{name:/Show more/}));
    expect(screen.getByText("COIN20")).toBeVisible();
    expect(screen.queryByText("COIN40")).toBeNull();
  });
  it("shows previous results by default and filters by study and recorded outcome", async () => {
    const ownerRead=vi.fn().mockResolvedValue({records:[{kind:"closure_research",value:{studies:[
      {study_id:"new",status:"COMPLETED",candidates:[],units:[],last_progress_ms:1800000000000},
      {study_id:"old",status:"COMPLETED",units:[],last_progress_ms:1700000000000,candidates:[
        {result_id:"one",question:"Funding hypothesis",outcome:"BLOCKED_DATA",reason:"Inputs absent"},
        {result_id:"two",question:"Momentum hypothesis",outcome:"INCONCLUSIVE_POWER",reason:"Power insufficient"},
      ]},
    ]}}],unavailable:[]});
    const client=new QueryClient({defaultOptions:{queries:{retry:false}}});
    render(<QueryClientProvider client={client}><PreviewProvider adapter={{mode:"LIVE",ownerRead} as unknown as OwnerAdapter}><ResearchStudies /></PreviewProvider></QueryClientProvider>);
    expect(await screen.findByText("Funding hypothesis")).toBeVisible();
    expect(screen.getByText("Momentum hypothesis")).toBeVisible();
    fireEvent.change(screen.getByRole("combobox",{name:"Outcome"}),{target:{value:"BLOCKED_DATA"}});
    expect(screen.getByText("Funding hypothesis")).toBeVisible();
    expect(screen.queryByText("Momentum hypothesis")).toBeNull();
    fireEvent.change(screen.getByRole("combobox",{name:"Study"}),{target:{value:"new"}});
    expect(screen.getByText("No recorded results match these filters.")).toBeVisible();
    expect(document.querySelector("pre")).toBeNull();
  });
});
