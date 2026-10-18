/* Regressions against the actual chart renderer and section resolver. */
"use strict";
const assert = require("node:assert/strict"), fs = require("node:fs"), vm = require("node:vm"), path = require("node:path");
const source = fs.readFileSync(path.join(__dirname,"../web/app.js"),"utf8");
function extract(name){const match=new RegExp(`^  function ${name}\\(`,"m").exec(source);assert.ok(match,name);
  const tail=source.slice(match.index),next=tail.search(/\n  (?:function |async function |const |let )/);
  return next>=0?tail.slice(0,next):tail;}
class Element{constructor(){this.children=[];this.attributes={};this.textContent="";this.clientWidth=983;this.hidden=false;}
  setAttribute(k,v){this.attributes[k]=String(v);}append(e){this.children.push(e);}replaceChildren(){this.children=[];}}
function harness(){const elements=new Map();const context={
  $:id=>{if(!elements.has(id))elements.set(id,new Element());return elements.get(id);},
  view:{w:146,h:82},selectedCar:"CAR002",number:(v,f=0)=>Number.isFinite(Number(v))?Number(v):f,
  hasValue:v=>v!==null&&v!==undefined&&Number.isFinite(Number(v)),
  fmt:(v,d=1)=>Number(v).toFixed(d),empty:e=>e.replaceChildren(),colorFor:()=>"#123456",
  node:(tag,attrs,parent,text)=>{const e=new Element();e.tag=tag;e.attributes=attrs;e.textContent=text;parent.append(e);return e;}};
  vm.createContext(context);vm.runInContext(extract("renderChart")+"\n"+extract("consoleSectionTarget")+"\n"+extract("yardMapPixel"),context);
  return {context,el:context.$};}
let count=0;function test(name,fn){fn();count++;console.log("PASS "+name);}
const items=[{id:"yard-area",top:-100,bottom:500},{id:"vehicle-panel",top:-100,bottom:650},
  {id:"trip-panel",top:700,bottom:2000},{id:"operations-panel",top:2100,bottom:3000}];
test("parallel inspector does not steal normal yard navigation",()=>assert.equal(harness().context.consoleSectionTarget(items,80,"#yard-area"),"yard-area"));
test("explicit inspector stays selected while its content covers the guide",()=>assert.equal(harness().context.consoleSectionTarget(items,80,"#vehicle-panel"),"vehicle-panel"));
test("scrolling below inspector selects the next main section",()=>assert.equal(harness().context.consoleSectionTarget(items.map(v=>({...v,top:v.top-800,bottom:v.bottom-800})),80,"#vehicle-panel"),"trip-panel"));
test("initial page above first section selects yard",()=>assert.equal(harness().context.consoleSectionTarget(items.map(v=>({...v,top:v.top+300,bottom:v.bottom+300})),80),"yard-area"));
test("gap between sections retains preceding main section",()=>assert.equal(harness().context.consoleSectionTarget(items,600),"yard-area"));
test("exact guide boundary selects the section",()=>assert.equal(harness().context.consoleSectionTarget([{id:"trip-panel",top:80,bottom:300}],80),"trip-panel"));
test("bottom of short final section selects it even when anchor cannot reach top",()=>assert.equal(harness().context.consoleSectionTarget([{id:"logs-panel",top:-400,bottom:200},{id:"scenario-editor",top:250,bottom:600}],80,"#logs-panel",true),"scenario-editor"));
test("empty section list has a stable default",()=>assert.equal(harness().context.consoleSectionTarget([],80),"yard-area"));
test("unknown hash does not create a missing active section",()=>assert.equal(harness().context.consoleSectionTarget(items,80,"#missing"),"yard-area"));
test("wide chart uses real width and fixed vertical coordinates",()=>{const h=harness();h.context.renderChart([{sim_time:1,speed:2},{sim_time:2,speed:3}]);assert.equal(h.el("car-chart").attributes.viewBox,"0 0 983 180");const coords=h.el("chart-line").attributes.d;assert.match(coords,/L969\.00,/);assert.ok(!coords.includes("NaN"));});
test("narrow chart keeps samples and axes inside its bounds",()=>{const h=harness();h.el("car-chart").clientWidth=310;h.context.renderChart([{sim_time:1,speed:2},{sim_time:2,speed:3}]);assert.equal(h.el("car-chart").attributes.viewBox,"0 0 310 180");assert.match(h.el("chart-line").attributes.d,/L296\.00,/);assert.ok(h.el("chart-labels").children.every(e=>!e.attributes.y||e.attributes.y<=180));});
test("zero width before layout uses the stable default",()=>{const h=harness();h.el("car-chart").clientWidth=0;h.context.renderChart([]);assert.equal(h.el("car-chart").attributes.viewBox,"0 0 500 180");});
test("chart identifies the selected car and last received speed",()=>{const h=harness();h.context.renderChart([{sim_time:2,speed:1.5}]);assert.equal(h.el("car-chart-title").textContent,"Sebesség időben · CAR002");assert.equal(h.el("chart-latest").textContent,"1.5 m/s");});
test("clearing car also clears plotted samples and last value",()=>{const h=harness();h.context.renderChart([{sim_time:1,speed:2}]);h.context.selectedCar=null;h.context.renderChart([]);assert.equal(h.el("chart-latest").textContent,"—");assert.equal(h.el("chart-line").attributes.d,"");assert.equal(h.el("car-chart-title").textContent,"Sebesség időben");assert.equal(h.el("car-chart-empty").hidden,false);});
test("invalid samples do not become plotted zeroes",()=>{const h=harness();h.context.renderChart([{sim_time:1,speed:null},{sim_time:null,speed:3},{sim_time:2,speed:"bad"}]);assert.equal(h.el("car-chart-empty").hidden,false);assert.equal(h.el("chart-latest").textContent,"—");});
test("single sample keeps a finite chart",()=>{const h=harness();h.context.renderChart([{sim_time:0,speed:0}]);assert.ok(!/NaN|Infinity/.test(h.el("chart-line").attributes.d));assert.equal(h.el("car-chart-empty").hidden,true);});
test("only trailing sixty seconds are plotted",()=>{const h=harness();h.context.renderChart([{sim_time:1,speed:8},{sim_time:100,speed:2},{sim_time:101,speed:1}]);assert.match(h.el("chart-caption").textContent,/^2 fogadott minta/);});
test("sample gaps remain separate SVG subpaths",()=>{const h=harness();h.context.renderChart([{sim_time:1,speed:2},{sim_time:1.1,speed:2},{sim_time:1.2,speed:2},{sim_time:6,speed:1}]);assert.equal((h.el("chart-line").attributes.d.match(/M/g)||[]).length,2);});
test("map scale follows height when a wide viewport letterboxes the SVG",()=>{const h=harness();h.el("yard-map").clientWidth=1500;h.el("yard-map").clientHeight=580;assert.equal(h.context.yardMapPixel(),82/580);});
test("map scale follows width when vertical space is abundant",()=>{const h=harness();h.el("yard-map").clientWidth=365;h.el("yard-map").clientHeight=400;assert.equal(h.context.yardMapPixel(),146/365);});
test("map before layout keeps a finite aspect-preserving fallback",()=>{const h=harness();h.el("yard-map").clientWidth=0;assert.equal(h.context.yardMapPixel(),146/900);});
console.log(`PASS ${count} console regressions`);
