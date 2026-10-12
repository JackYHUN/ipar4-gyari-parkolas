/* Render actual received server observations with the production frontend
   functions. The harness supplies a small SVG DOM, never derived traffic. */
"use strict";
const assert=require("node:assert/strict"),fs=require("node:fs"),path=require("node:path"),vm=require("node:vm");
const source=fs.readFileSync(path.join(__dirname,"../web/app.js"),"utf8");
class Element {
  constructor(tag="div"){
    this.tagName=tag;this.children=[];this.dataset={};this.attributes={};this._text="";this.className="";
    const classes=new Set();this.classList={toggle:(name,active)=>active?classes.add(name):classes.delete(name),contains:name=>classes.has(name)};
  }
  set textContent(value){this._text=String(value);this.children=[];}
  get textContent(){return this._text+this.children.map(child=>child.textContent).join("");}
  replaceChildren(...children){this._text="";this.children=children;}
  append(...children){this.children.push(...children);}
  setAttribute(key,value){this.attributes[key]=String(value);}
}
function extract(name){const match=new RegExp(`^  (?:async )?function ${name}\\(`,"m").exec(source);assert.ok(match,`${name} exists`);const tail=source.slice(match.index),next=tail.search(/\n  (?:function |async function |const |let )/);return next<0?tail:tail.slice(0,next);}
function harness(){
  const elements=new Map(),element=id=>{if(!elements.has(id)){const node=new Element(id.endsWith("-chart")?"svg":"div");node.id=id;elements.set(id,node);}return elements.get(id);};
  const ctx={$:element,NS:"http://www.w3.org/2000/svg",document:{createElementNS:(ns,tag)=>{assert.equal(ns,"http://www.w3.org/2000/svg");return new Element(tag);}},fmt:(value,digits=1)=>Number(value).toLocaleString("hu-HU",{minimumFractionDigits:digits,maximumFractionDigits:digits})};
  vm.createContext(ctx);for(const name of ["node","empty","capacityChartSegments","drawCapacityChart","renderTransportCapacity","markTransportCapacityOffline"])vm.runInContext(extract(name),ctx,{filename:`app:${name}`});
  return {ctx,elements};
}
function point(elapsed_s,changes={}){return {elapsed_s,window_s:1,arrival_rate_hz:10,dequeue_rate_hz:8,queue_messages:20,queue_growth_hz:2,snapshot_age_s:.1,...changes};}
function state(changes={},run_id="RUN-A"){return {run_id,mqtt:{capacity:{status:"observed",sample_age_s:.1,window_s:5,arrival_rate_hz:10,dequeue_rate_hz:8,queue_messages:20,queue_growth_hz:2,history_limit:120,sample_interval_s:1,window_target_s:5,clock:"host_perf_counter",scope:"current_run_bounded_in_memory_observations",reset_reason:"new_run",history:[point(0,{window_s:null,arrival_rate_hz:null,dequeue_rate_hz:null,queue_messages:0}),point(1),point(2)],...changes}}};}
function nodes(element,tag){return element.children.flatMap(child=>[...(child.tagName===tag?[child]:[]),...nodes(child,tag)]);}
function series(element,tag,field){return nodes(element,tag).filter(node=>node.attributes["data-series"]===field);}
function lines(h,field){return series(h.ctx.$("transport-rate-chart"),"polyline",field);}
function dots(h,chart,field){return series(h.ctx.$(chart),"circle",field);}
function plain(value){return JSON.parse(JSON.stringify(value));}
function freeze(value){if(value&&typeof value==="object"){Object.freeze(value);for(const child of Object.values(value))freeze(child);}return value;}
function pollHarness(){
  const h=harness(),c=h.ctx;Object.assign(c,{current:null,serverReachable:true,lastFetchOk:0,observedRun:"RUN-A",now:0,reply:null,connections:[],delays:[],controlRenders:0,carRenders:0,sensorRenders:0,
    getJSON:async url=>{assert.equal(url,"/api/state");if(c.reply instanceof Error)throw c.reply;return c.reply;},performance:{now:()=>c.now},
    // Operations safety has its own production-function harness; it is an unrelated poll collaborator here.
    renderOperationsSafety:()=>{},
    resetRunCaches:run=>{c.observedRun=run;},render:received=>{c.current=received;c.renderTransportCapacity(received);},refreshControls:()=>c.controlRenders++,renderCarDetails:()=>c.carRenders++,renderSensors:()=>c.sensorRenders++,setConnection:(...args)=>c.connections.push(args),setTimeout:(fn,ms)=>{assert.equal(fn,c.poll);c.delays.push(ms);}});
  vm.runInContext(extract("poll"),c,{filename:"app:poll"});return h;
}
let checks=0;function test(name,fn){fn();checks++;process.stdout.write(`PASS ${name}\n`);}

test("only declared server clock and current run observations are accepted; old payload cannot reuse a chart",()=>{
  const h=harness(),c=h.ctx;c.renderTransportCapacity(state());assert.equal(lines(h,"arrival_rate_hz").length,1);
  for(const old of [{},{run_id:"RUN-A",mqtt:{}},{mqtt:state().mqtt}]){c.renderTransportCapacity(old);assert.equal(c.$("transport-arrival-rate").textContent,"—");assert.equal(c.$("transport-queue-count").textContent,"—");assert.equal(nodes(c.$("transport-rate-chart"),"polyline").length,0);assert.equal(nodes(c.$("transport-queue-chart"),"circle").length,0);assert.equal(c.$("transport-capacity-status").textContent,"Mérés nem érhető el");}
  for(const change of [{clock:"simulation"},{scope:"all_runs"},{status:"invented"},{history:null},{history:Array(121).fill(point(0))}]){c.renderTransportCapacity(state(change));assert.equal(c.$("transport-arrival-rate").textContent,"—");assert.equal(nodes(c.$("transport-rate-chart"),"circle").length,0);}
});
test("genuine zero rates, queue and growth remain visible observations",()=>{
  const h=harness(),c=h.ctx;c.renderTransportCapacity(state({arrival_rate_hz:0,dequeue_rate_hz:0,queue_messages:0,queue_growth_hz:0,history:[point(0,{arrival_rate_hz:0,dequeue_rate_hz:0,queue_messages:0}),point(1,{arrival_rate_hz:0,dequeue_rate_hz:0,queue_messages:0})]}));
  assert.equal(c.$("transport-arrival-rate").textContent,"0,0");assert.equal(c.$("transport-dequeue-rate").textContent,"0,0");assert.equal(c.$("transport-queue-count").textContent,"0");assert.ok(c.$("transport-queue-change").textContent.includes("változatlan · 0,0"));assert.equal(c.$("transport-capacity-status").textContent,"A sor üres a mintában");assert.equal(lines(h,"arrival_rate_hz").length,1);assert.equal(dots(h,"transport-queue-chart","queue_messages")[0].attributes["data-value"],"0");
});
test("null, coercible text, booleans and nonfinite rates never become a fabricated zero",()=>{
  const h=harness(),c=h.ctx;
  for(const invalid of [null,undefined,"",true,"0",-.1,NaN,Infinity]){c.renderTransportCapacity(state({arrival_rate_hz:invalid,dequeue_rate_hz:invalid,queue_messages:invalid,queue_growth_hz:invalid,history:[point(0,{arrival_rate_hz:invalid,dequeue_rate_hz:invalid,queue_messages:invalid})]}));assert.equal(c.$("transport-arrival-rate").textContent,"—");assert.equal(c.$("transport-dequeue-rate").textContent,"—");assert.equal(c.$("transport-queue-count").textContent,"—");assert.equal(nodes(c.$("transport-rate-chart"),"polyline").length,0);assert.equal(nodes(c.$("transport-rate-chart"),"circle").length,0);assert.equal(nodes(c.$("transport-queue-chart"),"circle").length,0);}
});
test("queue count must be a whole safe count, while fractional nonnegative rates remain valid",()=>{
  const c=harness().ctx;for(const queue_messages of [1.5,Number.MAX_SAFE_INTEGER+1]){c.renderTransportCapacity(state({queue_messages,arrival_rate_hz:.25}));assert.equal(c.$("transport-queue-count").textContent,"—");assert.equal(c.$("transport-arrival-rate").textContent,"0,3");}
});
test("a negative queue growth is an observed draining trend, not an invalid rate or overload alarm",()=>{
  const c=harness().ctx;c.renderTransportCapacity(state({queue_messages:12,queue_growth_hz:-4.25}));assert.ok(c.$("transport-queue-change").textContent.includes("csökkent · -4,3 üzenet/s"));assert.equal(c.$("transport-capacity-status").textContent,"A sor ürül a mért ablakban");assert.equal(c.$("transport-capacity-status").className,"data-badge good");assert.ok(!c.$("transport-capacity-note").textContent.includes("Ha a sor tovább nő"));
});
test("growing received queue has a scoped warning and references existing acknowledged controls",()=>{
  const c=harness().ctx;c.renderTransportCapacity(state());assert.equal(c.$("transport-capacity-status").textContent,"A sor nő a mért ablakban");assert.equal(c.$("transport-capacity-status").className,"data-badge warn");const note=c.$("transport-capacity-note").textContent;assert.ok(note.includes("nem igazolt maximális kapacitás"));assert.ok(note.includes("hálózati késleltetés"));assert.ok(note.includes("MQTT-visszajelzés igazolja"));assert.ok(note.includes("helyi monoton faliórán"));
});
test("initial collecting sample at elapsed zero has a real queue dot but no invented rate or duration",()=>{
  const h=harness(),c=h.ctx;c.renderTransportCapacity(state({status:"collecting",window_s:null,arrival_rate_hz:null,dequeue_rate_hz:null,queue_messages:0,queue_growth_hz:null,history:[point(0,{window_s:null,arrival_rate_hz:null,dequeue_rate_hz:null,queue_messages:0})]}));assert.equal(c.$("transport-arrival-rate").textContent,"—");assert.equal(c.$("transport-queue-count").textContent,"0");assert.equal(c.$("transport-capacity-status").textContent,"Mérési ablak gyűlik");assert.equal(nodes(c.$("transport-rate-chart"),"circle").length,0);const queue=c.$("transport-queue-chart"),dot=dots(h,"transport-queue-chart","queue_messages");assert.equal(dot.length,1);assert.equal(dot[0].attributes["data-time"],"0");assert.equal(dot[0].attributes.cx,"337");const ticks=nodes(queue,"text").filter(node=>node.textContent.endsWith(" s"));assert.deepEqual(ticks.map(node=>node.textContent),["0,0 s"]);
});
test("current cards cannot infer rates from an absent, invalid or too short host window",()=>{
  const c=harness().ctx;for(const window_s of [null,"5",true,0,.999,NaN,Infinity]){c.renderTransportCapacity(state({window_s}));assert.equal(c.$("transport-arrival-rate").textContent,"—");assert.ok(c.$("transport-queue-change").textContent.includes("nincs értelmezhető"));}c.renderTransportCapacity(state({window_s:1}));assert.equal(c.$("transport-arrival-rate").textContent,"10,0");
});
test("unavailable counters do not erase separately received historical queue observations",()=>{
  const h=harness(),c=h.ctx;c.renderTransportCapacity(state({status:"unavailable",window_s:null,arrival_rate_hz:null,dequeue_rate_hz:null,history:[point(0),point(1,{window_s:null,arrival_rate_hz:null,dequeue_rate_hz:null,queue_messages:25})]}));assert.equal(c.$("transport-arrival-rate").textContent,"—");assert.equal(c.$("transport-capacity-status").textContent,"Ráta még nem meghatározható");assert.equal(nodes(c.$("transport-queue-chart"),"polyline").length,1);assert.equal(lines(h,"arrival_rate_hz").length,0);assert.equal(dots(h,"transport-rate-chart","arrival_rate_hz").length,1);
});
test("stale measurements remain marked historical and cannot raise a fresh growing-queue message",()=>{
  const h=harness(),c=h.ctx;c.renderTransportCapacity(state({status:"stale",sample_age_s:3.2}));assert.equal(c.$("transport-arrival-rate").textContent,"10,0");assert.equal(c.$("transport-capacity-status").textContent,"Utolsó mérés elavult");assert.ok(c.$("transport-queue-change").textContent.startsWith("Korábbi megfigyelés"));assert.ok(c.$("transport-capacity-panel").classList.contains("is-stale"));assert.ok(c.$("transport-rate-chart").classList.contains("is-stale"));assert.ok(nodes(c.$("transport-rate-chart"),"title")[0].textContent.includes("elavult megfigyelés"));assert.ok(!c.$("transport-capacity-note").textContent.includes("Ha a sor tovább nő"));c.renderTransportCapacity(state());assert.equal(c.$("transport-rate-chart").classList.contains("is-stale"),false);
});
test("run change and backend counter-reset history replace plots instead of joining prior runs",()=>{
  const h=harness(),c=h.ctx;c.renderTransportCapacity(state({history:[point(80),point(81)]}));assert.equal(lines(h,"arrival_rate_hz")[0].attributes["data-end-time"],"81");c.renderTransportCapacity(state({status:"collecting",reset_reason:"counter_reset",window_s:null,history:[point(0,{window_s:null,arrival_rate_hz:null,dequeue_rate_hz:null,queue_messages:0})]},"RUN-B"));assert.equal(c.$("transport-capacity-panel").dataset.runId,"RUN-B");assert.equal(c.$("transport-rate-chart").dataset.runId,"RUN-B");assert.equal(nodes(c.$("transport-rate-chart"),"polyline").length,0);assert.equal(dots(h,"transport-queue-chart","queue_messages")[0].attributes["data-time"],"0");assert.ok(!c.$("transport-rate-chart").textContent.includes("RUN-A"));assert.ok(c.$("transport-capacity-window").textContent.includes("számláló-visszaállás miatt újraindult"));c.renderTransportCapacity(state({reset_reason:"clock_regression",history:[point(0)]},"RUN-B"));assert.ok(c.$("transport-capacity-window").textContent.includes("óra-visszalépés miatt újraindult"));assert.ok(c.$("transport-capacity-note").textContent.includes("mérési sorozat kezdetétől"));
});
test("irregular actual elapsed time determines x coordinates rather than browser polling positions",()=>{
  const h=harness(),c=h.ctx;c.renderTransportCapacity(state({history:[point(10),point(10.2),point(11.5)]}));const line=lines(h,"arrival_rate_hz")[0],points=line.attributes.points.split(" ").map(pair=>pair.split(",").map(Number));assert.equal(points.length,3);assert.equal(points[0][0],48);assert.equal(points.at(-1)[0],626);assert.ok(Math.abs(points[1][0]-125.07)<.02);assert.ok(points[1][0]<(points[0][0]+points[2][0])/2);const ticks=nodes(c.$("transport-rate-chart"),"text").filter(node=>node.textContent.endsWith(" s"));assert.equal(ticks[0].textContent,"10,0 s");assert.equal(ticks.at(-1).textContent,"11,5 s");
});
test("missing rate values split only that source series and do not fabricate queue gaps",()=>{
  const h=harness(),c=h.ctx;c.renderTransportCapacity(state({history:[point(0),point(1),point(2,{arrival_rate_hz:null}),point(3),point(4)]}));assert.deepEqual(lines(h,"arrival_rate_hz").map(node=>[node.attributes["data-start-time"],node.attributes["data-end-time"]]),[["0","1"],["3","4"]]);assert.equal(lines(h,"dequeue_rate_hz").length,1);assert.equal(nodes(c.$("transport-queue-chart"),"polyline").length,1);
});
test("long sampling gap produces distinct segments, never an interpolated continuous traffic trace",()=>{
  const h=harness(),c=h.ctx;c.renderTransportCapacity(state({history:[point(0),point(1),point(4),point(5)]}));assert.deepEqual(lines(h,"arrival_rate_hz").map(node=>[node.attributes["data-start-time"],node.attributes["data-end-time"]]),[["0","1"],["4","5"]]);assert.equal(nodes(c.$("transport-queue-chart"),"polyline").length,2);
});
test("unknown interval, invalid clocks and nonincreasing receipt order cannot be sorted into a continuous trace",()=>{
  const c=harness().ctx;assert.deepEqual(plain(c.capacityChartSegments([point(2),point(1),point(1),point(2)],"arrival_rate_hz",1,true)).map(segment=>segment.map(p=>p.elapsed_s)),[[2],[1],[1,2]]);for(const invalid of [null,"0",true,-1,NaN,Infinity])assert.deepEqual(plain(c.capacityChartSegments([point(0),point(invalid),point(1)],"arrival_rate_hz",1,true)).map(segment=>segment.map(p=>p.elapsed_s)),[[0],[1]]);assert.deepEqual(plain(c.capacityChartSegments([point(0),point(1),point(2)],"arrival_rate_hz",null,true)).map(segment=>segment.length),[1,1,1]);
});
test("rate window invalidity interrupts received historical rates even when a value is present",()=>{
  const c=harness().ctx;for(const window_s of [null,true,"1",0,.5,Infinity])assert.deepEqual(plain(c.capacityChartSegments([point(0),point(1,{window_s}),point(2)],"arrival_rate_hz",1,true)).map(segment=>segment.map(p=>p.elapsed_s)),[[0],[2]]);
});
test("units and scopes remain explicit and rate series share an axis separate from queue counts",()=>{
  const h=harness(),c=h.ctx;c.renderTransportCapacity(state({arrival_rate_hz:2,dequeue_rate_hz:1,queue_messages:10000,history:[point(0,{arrival_rate_hz:2,dequeue_rate_hz:1,queue_messages:10000}),point(1,{arrival_rate_hz:2,dequeue_rate_hz:1,queue_messages:10001})]}));assert.equal(c.$("transport-rate-chart").dataset.unit,"üzenet/s");assert.equal(c.$("transport-queue-chart").dataset.unit,"üzenet");assert.notEqual(lines(h,"arrival_rate_hz")[0].attributes.stroke,lines(h,"dequeue_rate_hz")[0].attributes.stroke);assert.equal(lines(h,"dequeue_rate_hz")[0].attributes["stroke-dasharray"],"5 3");const rateY=Number(lines(h,"arrival_rate_hz")[0].attributes.points.split(" ")[0].split(",")[1]);assert.ok(rateY<70,"large queue does not flatten the independent rate axis");assert.ok(c.$("transport-capacity-note").textContent.includes("összes feliratkozott üzenettípus"));
});
test("no wall-age value is coerced into fresh zero and repeated polls do not add observations",()=>{
  const h=harness(),c=h.ctx;for(const age of [null,undefined,true,"0",-.1,NaN,Infinity]){c.renderTransportCapacity(state({sample_age_s:age}));assert.ok(c.$("transport-capacity-window").textContent.includes("minta kora: ismeretlen"));}const fixed=freeze(state());c.renderTransportCapacity(fixed);const a=plain(c.$("transport-rate-chart").children.map(node=>({tag:node.tagName,attrs:node.attributes,text:node.textContent})));c.renderTransportCapacity(fixed);const b=plain(c.$("transport-rate-chart").children.map(node=>({tag:node.tagName,attrs:node.attributes,text:node.textContent})));assert.deepEqual(a,b);assert.ok(c.$("transport-capacity-window").textContent.includes("3 megőrzött szerveroldali időpont"));
});
test("drawing never mutates or sorts frozen server evidence",()=>{
  const h=harness(),c=h.ctx,received=freeze(state({history:[point(0),point(1),point(1),point(2,{arrival_rate_hz:null}),point(4)]})),before=JSON.stringify(received);c.renderTransportCapacity(received);c.capacityChartSegments(received.mqtt.capacity.history,"queue_messages",1,false);assert.equal(JSON.stringify(received),before);
});
test("bounded source history and extreme finite observations produce bounded finite SVG geometry",()=>{
  const h=harness(),c=h.ctx;c.renderTransportCapacity(state({history:Array.from({length:120},(_,index)=>point(index))}));assert.equal(lines(h,"arrival_rate_hz")[0].attributes.points.split(" ").length,120);assert.ok(c.$("transport-rate-chart").children.length<30);c.renderTransportCapacity(state({history:[point(0,{arrival_rate_hz:Number.MAX_VALUE}),point(1,{arrival_rate_hz:Number.MAX_VALUE})]}));for(const node of nodes(c.$("transport-rate-chart"),"polyline"))assert.ok(!/NaN|Infinity/.test(node.attributes.points));for(const node of nodes(c.$("transport-rate-chart"),"circle"))assert.ok(Number.isFinite(Number(node.attributes.cx))&&Number.isFinite(Number(node.attributes.cy)));
});
async function integrationChecks(){
  async function testAsync(name,fn){await fn();checks++;process.stdout.write(`PASS ${name}\n`);}
  await testAsync("persistent HTTP failure marks preserved measurements historical without mutating source or recomputing age",async()=>{
    const h=pollHarness(),c=h.ctx,received=freeze(state());c.current=received;c.renderTransportCapacity(received);const before=JSON.stringify(received),rate=c.$("transport-arrival-rate").textContent,plot=JSON.stringify(lines(h,"arrival_rate_hz").map(node=>node.attributes));c.reply=new Error("HTTP unavailable");c.now=1501;await c.poll();assert.equal(c.serverReachable,false);assert.equal(c.current,received);assert.equal(JSON.stringify(received),before);assert.equal(c.$("transport-arrival-rate").textContent,rate);assert.equal(JSON.stringify(lines(h,"arrival_rate_hz").map(node=>node.attributes)),plot);assert.equal(c.$("transport-capacity-status").textContent,"Utolsó mérés · nincs frissítés");assert.equal(c.$("transport-capacity-panel").dataset.httpUnavailable,"true");for(const id of ["transport-capacity-panel","transport-rate-chart","transport-queue-chart"])assert.ok(c.$(id).classList.contains("is-stale"));assert.ok(c.$("transport-queue-change").textContent.includes("jelenlegi változása nem ellenőrizhető"));assert.ok(!c.$("transport-capacity-note").textContent.includes("Ha a sor tovább nő"));assert.ok(!c.$("transport-capacity-window").textContent.includes("minta kora: 0,10"));assert.equal(c.lastFetchOk,0);assert.deepEqual(c.delays,[250]);
  });
  await testAsync("brief HTTP errors preserve the existing 1500ms threshold instead of falsely marking the panel disconnected",async()=>{
    const h=pollHarness(),c=h.ctx;c.renderTransportCapacity(state());c.reply=new Error("brief error");for(const now of [1000,1500]){c.now=now;await c.poll();assert.equal(c.$("transport-capacity-panel").dataset.httpUnavailable,"false");assert.equal(c.$("transport-capacity-panel").classList.contains("is-stale"),false);assert.equal(c.$("transport-capacity-status").textContent,"A sor nő a mért ablakban");}c.now=1501;await c.poll();assert.equal(c.$("transport-capacity-panel").dataset.httpUnavailable,"true");
  });
  await testAsync("a successful loading state updates the measurement series even without a map and clears a prior run",async()=>{
    const h=pollHarness(),c=h.ctx;c.renderTransportCapacity(state({history:[point(80),point(81)]}));c.reply=freeze(state({status:"collecting",window_s:null,arrival_rate_hz:null,dequeue_rate_hz:null,queue_messages:0,history:[point(0,{window_s:null,arrival_rate_hz:null,dequeue_rate_hz:null,queue_messages:0})]},"RUN-B"));c.now=2000;await c.poll();assert.equal(c.current,c.reply);assert.equal(c.observedRun,"RUN-B");assert.equal(c.serverReachable,true);assert.equal(c.lastFetchOk,2000);assert.equal(c.$("transport-capacity-panel").dataset.runId,"RUN-B");assert.equal(c.$("transport-arrival-rate").textContent,"—");assert.equal(c.$("transport-queue-count").textContent,"0");assert.equal(lines(h,"arrival_rate_hz").length,0);assert.equal(dots(h,"transport-queue-chart","queue_messages")[0].attributes["data-time"],"0");assert.equal(c.$("run-status").textContent,"Várakozás az MQTT-adatútra…");c.reply=freeze({run_id:"RUN-C",connected:false,mqtt:{}});c.now=2100;await c.poll();assert.equal(c.$("transport-capacity-panel").dataset.runId,"RUN-C");assert.equal(c.$("transport-queue-count").textContent,"—");assert.equal(dots(h,"transport-queue-chart","queue_messages").length,0);assert.equal(c.$("transport-capacity-status").textContent,"Mérés nem érhető el");
  });
  await testAsync("fresh HTTP recovery replaces the offline presentation solely from the next received measurement",async()=>{
    const h=pollHarness(),c=h.ctx;c.current=freeze(state());c.renderTransportCapacity(c.current);c.reply=new Error("offline");c.now=1800;await c.poll();c.reply=freeze({...state({arrival_rate_hz:3,dequeue_rate_hz:4,queue_messages:4,queue_growth_hz:-1,history:[point(4,{arrival_rate_hz:3}),point(5,{arrival_rate_hz:3})]}),map:{}});c.now=2100;await c.poll();assert.equal(c.$("transport-arrival-rate").textContent,"3,0");assert.equal(c.$("transport-capacity-status").textContent,"A sor ürül a mért ablakban");for(const id of ["transport-capacity-panel","transport-rate-chart","transport-queue-chart"]){assert.equal(c.$(id).classList.contains("is-stale"),false);assert.equal(c.$(id).dataset.httpUnavailable,"false");}assert.equal(c.lastFetchOk,2100);assert.ok(c.$("transport-capacity-window").textContent.includes("minta kora: 0,10 s"));assert.ok(!c.$("transport-capacity-note").textContent.includes("kapcsolat helyreállásáig"));
  });
  await testAsync("old cached HTML and partially missing new elements cannot turn a successful loading poll into an HTTP failure",async()=>{
    const h=pollHarness(),c=h.ctx,element=c.$;c.$=id=>id.startsWith("transport-")?null:element(id);assert.doesNotThrow(()=>c.markTransportCapacityOffline());c.reply=freeze(state());c.now=100;await c.poll();assert.equal(c.serverReachable,true);assert.equal(c.current,c.reply);assert.equal(c.$("run-status").textContent,"Várakozás az MQTT-adatútra…");c.reply=new Error("offline");c.now=1700;await c.poll();assert.equal(c.serverReachable,false);assert.equal(c.$("notice").hidden,false);c.$=id=>id==="transport-dequeue-rate"?null:element(id);assert.doesNotThrow(()=>c.renderTransportCapacity(state()));assert.doesNotThrow(()=>c.markTransportCapacityOffline());assert.equal(element("transport-capacity-status").textContent,"Utolsó mérés · nincs frissítés");
  });
  process.stdout.write(`${checks} frontend capacity checks passed.\n`);
}
integrationChecks().catch(error=>{process.stderr.write(`${error.stack}\n`);process.exitCode=1;});
