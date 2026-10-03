import {render, screen, waitFor} from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import {QueryClient, QueryClientProvider} from "@tanstack/react-query";
import {describe,it,expect,vi,afterEach} from "vitest";
import {PreviewProvider} from "../src/context";
import {NeedsYou, OwnerEvidence} from "../src/components/OwnerEvidence";
import {createLiveAdapter, mapKnowledge} from "../src/adapters/live";
import type {OwnerAdapter} from "../src/adapters/contracts";
import * as pending from "../src/adapters/pending";

const hash="b".repeat(64), id="a".repeat(64);
const item={item_id:id,binding_hash:hash,action_type:"paid_spend",affected_object:"TEST-ONLY",reason:"Exact test cost",validity:"VALID",receipt:null,decision_operation:"approval_decision"};
const feed={generated_at:new Date().toISOString(),schema:"owner-needs-you.v1",items:[item],unavailable:[]};
function mount(adapter: Partial<OwnerAdapter>, node=<NeedsYou/>) {
  const client=new QueryClient({defaultOptions:{queries:{retry:false}}});
  return render(<QueryClientProvider client={client}><PreviewProvider adapter={{mode:"LIVE",...adapter} as OwnerAdapter}>{node}</PreviewProvider></QueryClientProvider>);
}
afterEach(()=>{vi.unstubAllGlobals(); pending.entries().forEach(r=>pending.done(r.id));});
describe("Stage8 owner evidence",()=>{
  it("sends exact binding through adapter and keeps same id after unknown outcome",async()=>{
    const read=vi.fn().mockResolvedValue(feed);
    const action=vi.fn().mockResolvedValue({status:"OUTCOME_UNKNOWN",reasons:["lost_result"]});
    mount({ownerRead:read,approvalDecision:action});
    const user=userEvent.setup();
    await user.click(await screen.findByRole("button",{name:"Approve exact request"}));
    await waitFor(()=>expect(action).toHaveBeenCalledTimes(1));
    await user.click(screen.getByRole("button",{name:"Approve exact request"}));
    expect(action.mock.calls[0][0]).toEqual({item_id:id,binding_hash:hash,decision:"APPROVED"});
    expect(action.mock.calls[0][1]).toEqual(action.mock.calls[1][1]);
  });
  it("stale approvals cannot be submitted",async()=>{
    const action=vi.fn(); mount({ownerRead:vi.fn().mockResolvedValue({...feed,items:[{...item,validity:"STALE"}]}),approvalDecision:action});
    expect(await screen.findByRole("button",{name:"Approve exact request"})).toBeDisabled();
    expect(action).not.toHaveBeenCalled();
  });
  it("an expired validity window disables a cached valid approval",async()=>{
    const action=vi.fn(); mount({ownerRead:vi.fn().mockResolvedValue({...feed,items:[{...item,valid_until_ms:Date.now()-1}]}),approvalDecision:action});
    expect(await screen.findByRole("button",{name:"Approve exact request"})).toBeDisabled();
    expect(await screen.findByText(/STALE — expired/)).toBeVisible();
    expect(action).not.toHaveBeenCalled();
  });
  it("missing query evidence is unavailable, without fixtures",async()=>{
    mount({ownerRead:vi.fn().mockResolvedValue({generated_at:feed.generated_at,status:"UNAVAILABLE",records:[],unavailable:[{field:"decision",reason:"NOT_RECORDED"}]})},<OwnerEvidence/>);
    await userEvent.setup().click(screen.getByText("Interrogate exact internal evidence"));
    expect(await screen.findByText(/NOT_RECORDED/)).toBeVisible();
    expect(screen.queryByText(/Synthetic|fixture equity/)).toBeNull();
  });
  it("Evidence, Code and Timeline retain distinct membership and chronology",()=>{
    const d=mapKnowledge({nodes:[{id:"record:d",label:"d",kind:"Decision",lenses:["Evidence","Timeline"],provenance:{id:"record:d",source:"decisions",time_basis:"event"}},{id:"code:x.py",label:"x.py",kind:"Code",lenses:["Code"],provenance:{id:"code:x.py",source:"x.py",time_basis:"NOT_RECORDED"}}],edges:[],lenses:{Evidence:{available:true},Timeline:{available:true},Code:{available:true},Knowledge:{available:true}}});
    expect(d.nodes[0].lenses).toEqual(["Evidence","Timeline"]);
    expect(d.nodes[0].evidence.timeBasis).toBe("event");
    expect(d.nodes[1].lenses).toEqual(["Code"]);
  });
  it("LIVE chat preserves the queried record hashes instead of inventing provenance",async()=>{
    vi.stubGlobal("fetch",vi.fn().mockResolvedValue(new Response(JSON.stringify({reply:"Recorded skip [decision:d]",operational:false,evidence:[{record_id:"decision:d",source:"decisions",sha256:hash,verification:"RECORDED",value:{skip_reason:"cost"},timestamp:"2026-10-03T00:00:00Z"}]}))));
    const out=await createLiveAdapter(()=>{}).chat("why skip","normal",new AbortController().signal,()=>{});
    expect(out.evidence[0].id).toBe("decision:d");
    expect(out.evidence[0].classification).toContain(hash);
  });
});

it("Needs You reports full pending count and exposes pages beyond completed history",async()=>{
  const read=vi.fn((path:string)=>Promise.resolve(path.includes("offset=100") ? {...feed,items:[{...item,item_id:"older",reason:"Older pending action"}],pending_total:101,total:101,truncated:false} : {...feed,pending_total:101,total:101,truncated:true}));
  mount({ownerRead:read});
  expect(await screen.findByText(/101 pending or stale owner items/)).toBeVisible();
  await userEvent.setup().click(screen.getByRole("button",{name:"More owner items"}));
  expect(await screen.findByText("Older pending action")).toBeVisible();
  expect(read.mock.calls.some(c=>c[0]==="needs-you?offset=100")).toBe(true);
});

it("typed research catalog navigates to the oldest question page",async()=>{
  const read=vi.fn((path:string)=>Promise.resolve({generated_at:feed.generated_at,status:"AVAILABLE",records:[{record_id:path.includes("offset=50") ? "question:q0" : "question:q50",verification:"RECORDED"}],questions_page:{offset:path.includes("offset=50") ? 50 : 0,has_more:!path.includes("offset=50"),next_offset:50}}));
  mount({ownerRead:read},<OwnerEvidence/>);
  await userEvent.setup().click(screen.getByText("Interrogate exact internal evidence"));
  await userEvent.setup().selectOptions(screen.getByLabelText("Evidence class"),"research");
  await userEvent.setup().click(screen.getByRole("button",{name:"Read evidence"}));
  await userEvent.setup().click(await screen.findByRole("button",{name:"More questions"}));
  expect((await screen.findAllByText(/question:q0/))[0]).toBeVisible();
  expect(read.mock.calls.some(c=>c[0]==="query/research?offset=50")).toBe(true);
});

it("questions-only Research responses are typed partial evidence; malformed records fail closed",async()=>{
  const {readContractIssue}=await import("../src/adapters/readContracts");
  const body={available:true,partial:true,reason:"quantitative tables absent",questions:[{record_id:"question:q0",identity:"q0",source:"research_questions",sha256:hash,value:{status:"INCONCLUSIVE"}}],questions_page:{offset:0,limit:50,has_more:false}};
  expect(readContractIssue("research",body)).toBeNull();
  expect(readContractIssue("research",{...body,questions:[{...body.questions[0],identity:null}]})).toContain("identity");
});
