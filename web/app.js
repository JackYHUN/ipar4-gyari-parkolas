"use strict";
(() => {
  const $ = id => document.getElementById(id);
  const NS = "http://www.w3.org/2000/svg";
  const stateNames = {waiting:"Parkolóhelyre vár",driving:"Úton",waiting_zone:"Zónaengedélyre vár",pedestrian_stop:"Gyalogosra vár",parking:"Beparkol",parked:"Parkol",communication_stop:"Kommunikációs megállás",sensor_stop:"Érzékelő miatti megállás",frozen:"Injektált megállás"};
  const displayState = car => number(car.speed)>.05 && ['pedestrian_stop','sensor_stop','communication_stop','frozen'].includes(car.state)
    ? `Fékez · ${stateNames[car.state]}` : number(car.speed)<.05 && car.state==='driving' && car.wait_reason ? car.wait_reason : stateNames[car.state]||car.state;
  const palette = ["#2f78bb", "#088f8e", "#bb7653", "#8263b9", "#52799d", "#ac638d"];
  const parkingColors = {free:["#e2f2e9","#71ab91"],reserved:["#fff0d2","#d1aa57"],occupied:["#e4eaf3","#8096b4"],blocked:["#f8e6e4","#d3938c"],unknown:["#e9edf0","#a0adb6"]};
  let current = null, selectedCar = null, selectedSlot = null, mapKey = null, busy = false, serverReachable = false, lastFetchOk = 0, toastTimer;
  const carNodes = new Map(), parkingNodes = new Map(), crossingNodes = new Map();
  const pendingControls = new Map();
  let scenarioData=null, scenarioDraft=null, previewMap=null, scenarioDirty=false, previewBusy=false, previewVersion=0, previewTimer;
  let coordinateEdit=null;
  let runSettingsDirty=false,settingsRun=null,pendingSettingsRun=null;
  const placementHelp="Válassz szerkesztési módot, majd kattints a térképre vagy add meg a koordinátákat. A szerver ellenőrzi az elérhetőséget és az átfedéseket.";
  let view={x:0,y:0,w:140,h:90}, fitView={x:0,y:0,w:140,h:90}, fullMap={width:140,height:90}, drag=null, followCar=false, movedPointer=false;
  const number = (value, fallback = 0) => Number.isFinite(Number(value)) ? Number(value) : fallback;
  function synchronizeRunSettings(state){
    if(!state?.run_id||!Number.isInteger(state.seed)||!["nearest","fixed"].includes(state.strategy))return;
    const acceptedRun=pendingSettingsRun===state.run_id;
    if(state.run_id===settingsRun&&!acceptedRun)return;
    if(!acceptedRun&&(runSettingsDirty||scenarioDirty))return;
    $("seed-input").value=state.seed;$("strategy-select").value=state.strategy;
    settingsRun=state.run_id;runSettingsDirty=false;
    if(acceptedRun)pendingSettingsRun=null;
  }
  const fmt = (value, digits = 1) => number(value).toLocaleString("hu-HU", {maximumFractionDigits:digits,minimumFractionDigits:digits});
  const timeText = value => { const seconds=Math.max(0,number(value)); return `${String(Math.floor(seconds/60)).padStart(2,"0")}:${String(Math.floor(seconds%60)).padStart(2,"0")}.${Math.floor((seconds%1)*10)}`; };
  function node(tag, attrs={}, parent=null, text=null) { const e=document.createElementNS(NS,tag);for(const [k,v] of Object.entries(attrs))e.setAttribute(k,String(v));if(text!==null)e.textContent=text;if(parent)parent.append(e);return e; }
  function colorFor(id) { const v=String(id).match(/\d+/);return palette[v?Math.max(0,Number(v[0])-1)%palette.length:String(id).length%palette.length]; }
  function empty(element) {element.replaceChildren();}
  function showToast(message, error=false) {clearTimeout(toastTimer);$("toast").textContent=message;$("toast").classList.toggle("error",error);$("toast").hidden=false;toastTimer=setTimeout(()=>$("toast").hidden=true,4000);}
  function setConnection(connected, text=null,transport=null) {const b=$("connection-badge");b.className=`status-badge ${connected?"":"offline"}`;b.replaceChildren(document.createElement("i"),document.createTextNode(text || (connected?"MQTT kapcsolat él":transport?.broker_connected===true?"MQTT él · állapotkép elavult":"MQTT-kapcsolat kiesett")));}
  function parkingState(slot) {const state=String(slot.state || "unknown");return state==="parked"?"occupied":state==="available"?"free":state==="unavailable"?"blocked":state;}
  function pointSegmentDistance(p,a,b){const dx=b[0]-a[0],dy=b[1]-a[1],length=dx*dx+dy*dy,t=length?Math.max(0,Math.min(1,((p[0]-a[0])*dx+(p[1]-a[1])*dy)/length)):0;return Math.hypot(p[0]-a[0]-t*dx,p[1]-a[1]-t*dy);}
  function segmentsOverlap(a,b,c,d){
    if(Math.abs(a[1]-b[1])<.05&&Math.abs(c[1]-d[1])<.05&&Math.abs(a[1]-c[1])<.05)return Math.min(Math.max(a[0],b[0]),Math.max(c[0],d[0]))-Math.max(Math.min(a[0],b[0]),Math.min(c[0],d[0]))>.1;
    if(Math.abs(a[0]-b[0])<.05&&Math.abs(c[0]-d[0])<.05&&Math.abs(a[0]-c[0])<.05)return Math.min(Math.max(a[1],b[1]),Math.max(c[1],d[1]))-Math.max(Math.min(a[1],b[1]),Math.min(c[1],d[1]))>.1;
    return pointSegmentDistance(a,c,d)<.05&&pointSegmentDistance(b,c,d)<.05;
  }
  function logisticsGeometry(map){
    if(!map.layout)return {...map,bounds:{x:0,y:0,w:number(map.width,140),h:number(map.height,90)}};
    const segments=(map.slots||[]).flatMap(s=>(s.route||[]).slice(1).map((p,i)=>[s.route[i],p]));
    if(!segments.length)return {...map,bounds:{x:0,y:0,w:number(map.width,600),h:number(map.height,400)}};
    const near=(x,y,margin=10)=>segments.some(([a,b])=>pointSegmentDistance([number(x),number(y)],a,b)<margin);
    const areaIds=new Set((map.slots||[]).map(s=>s.area_id));
    const roads=(map.roads||[]).filter(r=>{const pts=Array.isArray(r)?r:r.points||[];return pts.slice(1).some((p,i)=>segments.some(([a,b])=>segmentsOverlap(pts[i],p,a,b)));});
    const exits=(map.production_exits||[]).filter(e=>near(e.spawn?.[0]??e.x,e.spawn?.[1]??e.y,1));
    const buildings=(map.buildings||[]).filter(b=>['G70','G80'].includes(b.id));
    const areas=(map.parking_areas||[]).filter(a=>areaIds.has(a.id)||(map.slots||[]).some(s=>s.x>=a.x&&s.x<=a.x+a.w&&s.y>=a.y&&s.y<=a.y+a.h));
    const points=[...segments.flat(),...buildings.flatMap(b=>[[b.x,b.y],[b.x+b.w,b.y+b.h]]),...areas.flatMap(a=>[[a.x,a.y],[a.x+a.w,a.y+a.h]])];
    const minX=Math.max(0,Math.min(...points.map(p=>p[0]))-18),minY=Math.max(0,Math.min(...points.map(p=>p[1]))-16),maxX=Math.max(...points.map(p=>p[0]))+24,maxY=Math.min(number(map.height,400),Math.max(...points.map(p=>p[1]))+24);
    return {...map,roads,buildings,parking_areas:areas,production_exits:exits,crossings:(map.crossings||[]).filter(c=>near(c.x,c.y,14)),zones:(map.zones||[]).filter(z=>near(z.x,z.y,10)),bounds:{x:minX,y:minY,w:Math.max(40,maxX-minX),h:Math.max(40,maxY-minY)}};
  }
  // A single static renderer keeps the live and archived yard maps visually
  // identical while each view retains its own map, layers and run identity.
  function renderMapGeometry(map,layers,options={}){
    const scale=Math.max(1,map.bounds.w/190);
    const buildings=map.buildings?.length?map.buildings:[{x:3,y:39,w:22,h:36,label:"GYÁRTÁS"}];
    for(const b of buildings){const g=node("g",{class:`factory-building ${b.kind||"production"}`},layers.buildings);node("rect",{x:b.x,y:b.y,width:b.w,height:b.h,rx:1.3*scale,fill:{assembly:"#d1e1ed",engine:"#d8e0ed",paint:"#d9e6e2",logistics:"#e7e0d5",office:"#e1e7ed"}[b.kind]||"#dce5eb",stroke:"#bccbd6","stroke-width":.3*scale},g);node("rect",{x:number(b.x)+1,y:number(b.y)+1,width:Math.max(1,number(b.w)-2),height:Math.max(1,number(b.h)-2),rx:.6,fill:`url(#${options.roofId||"factory-roof"})`,opacity:.7},g);const tx=number(b.x)+number(b.w)/2,ty=number(b.y)+number(b.h)/2;const parts=String(b.label||b.id||"GYÁRTÁS").split(" · ");node("text",{x:tx,y:ty-(parts.length>1?1.3:.2)*scale,"text-anchor":"middle",class:"building-label"},g,parts[0]);if(parts.length>1||b.subtitle)node("text",{x:tx,y:ty+1.7*scale,"text-anchor":"middle",class:"building-subtitle"},g,parts.slice(1).join(" · ")||b.subtitle);node("title",{},g,b.label||b.id||"Gyártóépület");}
    for(const area of map.parking_areas||[]){const g=node("g",{},layers.buildings);node("rect",{x:area.x,y:area.y,width:area.w,height:area.h,rx:1.3*scale,fill:"#dfede7",stroke:"#b2cfc1","stroke-width":.3*scale,"stroke-dasharray":`${scale} ${scale}`},g);node("text",{x:number(area.x)+number(area.w)/2,y:number(area.y)+3.6*scale,"text-anchor":"middle",class:"parking-area-label"},g,area.label||area.id);}
    const roads=map.roads?.length?map.roads:[[[14,58],[122,58],[122,24],[36,24]]];
    for(const road of roads){const points=Array.isArray(road)?road:road.points||[],pts=points.map(p=>`${p[0]},${p[1]}`).join(" "),rw=number(road.width,9);node("polyline",{points:pts,fill:"none",stroke:"#d8e2e7","stroke-width":rw+1.2,"stroke-linejoin":"round","stroke-linecap":"round"},layers.roads);node("polyline",{points:pts,fill:"none",stroke:"#c9d6de","stroke-width":rw,"stroke-linejoin":"round","stroke-linecap":"round"},layers.roads);node("polyline",{points:pts,fill:"none",stroke:"#f7fafb","stroke-width":.25*scale,"stroke-dasharray":`${2*scale} ${1.7*scale}`,"stroke-linejoin":"round"},layers.roads);for(let i=1;i<points.length;i++){const a=points[i-1],b=points[i],length=Math.hypot(b[0]-a[0],b[1]-a[1]);if(length>18*scale){const angle=Math.atan2(b[1]-a[1],b[0]-a[0])*180/Math.PI;node("path",{d:"M-1.5,-1L0,0L-1.5,1M0,0H-3.5",transform:`translate(${(a[0]+b[0])/2} ${(a[1]+b[1])/2}) rotate(${angle}) scale(${scale})`,fill:"none",stroke:"#7892a5","stroke-width":.35},layers.annotations);}}if(road.label&&points.length>1){const a=points[0],b=points[1];node("text",{x:(a[0]+b[0])/2,y:(a[1]+b[1])/2-rw/2-1.5*scale,"text-anchor":"middle",class:"road-label"},layers.annotations,road.label);}}
    for(const s of map.slots||[]){const previous=s.route?.at(-2);if(previous)node("line",{x1:previous[0],y1:previous[1],x2:s.x,y2:s.y,stroke:"#d8e2e7","stroke-width":3.4,"stroke-linecap":"round"},layers.roads);else if(!map.layout)node("line",{x1:s.x,y1:24,x2:s.x,y2:s.y,stroke:"#d8e2e7","stroke-width":7,"stroke-linecap":"round"},layers.roads);}
    for(const z of map.zones||[]){const g=node("g",{"data-zone":z.id},layers.zones);node("rect",{x:number(z.x)-number(z.w,8)/2,y:number(z.y)-number(z.h,8)/2,width:number(z.w,8),height:number(z.h,8),rx:1,fill:"#edf1fa",stroke:"#92a6c9","stroke-width":.3,"stroke-dasharray":".8 .5",opacity:.75},g);node("text",{x:number(z.x)+number(z.w,8)/2+1,y:number(z.y)+number(z.h,8)/2+1.4,class:"map-label"},g,z.id);}
    for(const c of map.crossings||[]){const horizontal=c.axis==="horizontal";const g=node("g",{},layers.crossings);const marker=node("rect",{x:number(c.x)-(horizontal?6:4),y:number(c.y)-(horizontal?4:6),width:horizontal?12:8,height:horizontal?8:12,rx:1,fill:"#e7ebdf",stroke:"#9fad97","stroke-width":.2},g);for(let i=0;i<6;i++)node("rect",horizontal?{x:number(c.x)-5+i*1.75,y:number(c.y)-3.7,width:1,height:7.4,fill:"#fafcf9"}:{x:number(c.x)-3.7,y:number(c.y)-5+i*1.75,width:7.4,height:1,fill:"#fafcf9"},g);const label=String(c.id).replace("ZEBRA","ZEBRA ");node("text",{x:number(c.x)+(horizontal?8:6),y:number(c.y)+(horizontal?-.2:7.5),class:"map-label",fill:"#879980"},g,label);if(options.interactive)crossingNodes.set(c.id,marker);}
    const slotW=map.layout?3.4:6.6,slotH=map.layout?6.8:10.4;
    for(const s of map.slots||[]){
      const g=node("g",{class:`slot-group ${options.interactive?"":"archived-slot"}`,role:options.interactive?"button":"img",tabindex:options.interactive?0:-1,"aria-label":`Parkolóhely: ${s.id}`,"data-slot":s.id},layers.parking);
      node("rect",{x:number(s.x)-slotW/2,y:number(s.y)-slotH/2,width:slotW,height:slotH,rx:.7,class:"slot-outline"},g);
      node("path",{d:`M${number(s.x)-slotW/2+.5},${number(s.y)-slotH/2+.5}h${slotW-1}M${number(s.x)-slotW/2+.5},${number(s.y)+slotH/2-.5}h${slotW-1}`,fill:"none",stroke:"#f9fcfa","stroke-width":.35},g);
      node("text",{x:s.x,y:number(s.y)+.8,class:"slot-symbol"},g,"P");
      node("text",{x:s.x,y:number(s.y)+7.6,class:"slot-label"},g,s.id);
      node("title",{},g,options.interactive?s.id:`${s.id} · mentett parkolóhely-geometria; a pillanatnyi foglaltság nincs ebben a járműmintában`);
      if(!options.interactive)continue;
      parkingNodes.set(s.id,g);
      const select=()=>{selectedSlot=s.id;if(previewMap){$("placement-mode").value="slot";fillPlacementItems(s.id);}else{$("slot-select").value=s.id;renderParking();}};
      g.addEventListener("click",event=>{if(!movedPointer||event.detail===0)select();});g.addEventListener("keydown",e=>{if(e.key==="Enter"||e.key===" "){e.preventDefault();select();}});
    }
    const a=layers.annotations;for(const exit of map.production_exits||[]){const ex=exit.spawn?.[0]??exit.x,ey=exit.spawn?.[1]??exit.y;const g=node("g",{},a);node("circle",{cx:ex,cy:ey,r:1.6*scale,fill:"#168b9b",stroke:"white","stroke-width":.4*scale},g);node("text",{x:number(ex),y:number(ey)-4*scale,"text-anchor":"middle",class:"exit-label"},g,exit.label||exit.id);node("title",{},g,`Gyártósori kijárat: ${exit.label||exit.id}`);}const ruler=Math.max(10,Math.round(number(map.width,140)/10/10)*10),rx=map.bounds.x+8*scale,ry=map.bounds.y+map.bounds.h-7*scale;node("path",{d:`M${rx} ${ry}h${ruler}m-${ruler} 0v-${scale}m${ruler} ${scale}v-${scale}`,stroke:"#95a9b7","stroke-width":.2*scale},a);node("text",{x:rx+ruler/2,y:ry+3*scale,class:"map-label","text-anchor":"middle"},a,`${ruler} m`);if(!map.layout){node("text",{x:30,y:49.7,class:"map-label strong"},a,"KIJÁRAT → PARKOLÁS");node("text",{x:40,y:45.2,class:"map-label"},a,`PARKOLÓSÁV · ${(map.slots||[]).length} HELY`);}  }
  function tripMapDefinitions(parent){
    const defs=node("defs",{},parent);
    const roof=node("pattern",{id:"trip-factory-roof",width:2.4,height:2.4,patternUnits:"userSpaceOnUse"},defs);node("path",{d:"M0 2.4L2.4 0",stroke:"#c7d3db","stroke-width":.2},roof);
    const grid=node("pattern",{id:"trip-map-grid",width:5,height:5,patternUnits:"userSpaceOnUse"},defs);node("path",{d:"M5 0H0V5",fill:"none",stroke:"#dce4e8","stroke-width":.12},grid);
    const shadow=node("filter",{id:"trip-car-shadow",x:"-80%",y:"-80%",width:"260%",height:"260%"},defs);node("feDropShadow",{dx:0,dy:.35,stdDeviation:.25,"flood-color":"#122033","flood-opacity":.25},shadow);
  }
  function buildMap(map) {
    map=logisticsGeometry(map);
    for(const id of ["buildings-layer","roads-layer","zones-layer","crossings-layer","parking-layer","routes-layer","pedestrians-layer","sensors-layer","cars-layer","map-annotations","editor-layer"]) empty($(id));
    carNodes.clear();parkingNodes.clear();crossingNodes.clear();
    const width=number(map.width,140),height=number(map.height,90),scale=Math.max(1,map.bounds.w/190);fullMap={width,height};fitView={...map.bounds};view={...fitView};$("yard-map").style.aspectRatio=`${fitView.w}/${fitView.h}`;updateView();$("yard-map").querySelector(".map-background").setAttribute("width",width);$("yard-map").querySelector(".map-background").setAttribute("height",height);$("map-grid-background").setAttribute("width",width);$("map-grid-background").setAttribute("height",height);$("map-scale").textContent=`Forgalmi terület: ${fmt(fitView.w,0)} × ${fmt(fitView.h,0)} m · ${map.layout?.name||map.layout?.title||map.label||"zárt gyárudvar"}`;$("map-area-heading").textContent=`${map.production_exits?.[0]?.label||"Gyártósori kijárat"} → zebrák és forgalmi zónák → ${map.parking_areas?.[0]?.label||"készjármű-parkoló"}`;
    renderMapGeometry(map,{buildings:$("buildings-layer"),roads:$("roads-layer"),zones:$("zones-layer"),crossings:$("crossings-layer"),parking:$("parking-layer"),annotations:$("map-annotations")},{interactive:true});
    renderAttribution(map);if(previewMap)renderEditorMap();renderSensors();
  }
  function fillSelect(select, values, placeholder) {const prior=select.value;select.replaceChildren();if(!values.length){select.add(new Option(placeholder,""));return;}for(const v of values)select.add(new Option(v.label,v.value));if(values.some(v=>v.value===prior))select.value=prior;}
  function updateView(){const map=$("yard-map");map.setAttribute("viewBox",`${view.x} ${view.y} ${view.w} ${view.h}`);map.classList.toggle("map-wide",fullMap.width>250);map.classList.toggle("map-detail",view.w<240);map.style.setProperty("--map-label-scale",Math.max(.5,view.w/150));map.style.setProperty("--map-pixel",view.w/Math.max(260,map.clientWidth||900));$("map-zoom-out").disabled=view.w>=fitView.w*1.7;$("map-zoom-in").disabled=view.w<=30;const activeCar=(current?.cars||[]).some(car=>car.id===selectedCar);$("map-focus-car").disabled=Boolean(previewMap)||!activeCar;$("map-focus-car").classList.toggle("selected",followCar&&activeCar);layoutParkingLabels();renderSensorOverlay();}
  function layoutParkingLabels(){
    const pixel=view.w/Math.max(260,$("yard-map").clientWidth||900),states=new Map((current?.parking||[]).map(s=>[s.id,s]));
    const labels=[...parkingNodes].map(([id,g])=>{const label=g.querySelector(".slot-label"),slot=states.get(id),highlighted=g.classList.contains("selected")||Boolean(!previewMap&&selectedCar&&slot?.car_id===selectedCar),occupied=previewMap?previewMap.initial_occupied?.includes(id):slot&&["occupied","reserved","blocked"].includes(parkingState(slot));return {g,id,x:number(label.getAttribute("x")),y:number(label.getAttribute("y")),width:(id.length*5+6)*pixel,highlighted,priority:highlighted?0:occupied?1:2};}).sort((a,b)=>a.priority-b.priority||a.y-b.y||a.x-b.x);
    const shown=[];for(const label of labels){const overlaps=shown.some(other=>Math.abs(label.y-other.y)<11*pixel&&Math.abs(label.x-other.x)<(label.width+other.width)/2);const visible=label.highlighted||!overlaps;label.g.classList.toggle("sparse-slot-label",!visible);label.g.classList.toggle("highlighted-slot-label",label.highlighted);if(visible)shown.push(label);}
  }
  function fitMap(){followCar=false;view={...fitView};updateView();}
  function zoomMap(factor,center=null){followCar=false;const nw=Math.max(30,Math.min(fitView.w*1.8,view.w*factor)),nh=nw*fitView.h/fitView.w,cx=center?.x??view.x+view.w/2,cy=center?.y??view.y+view.h/2;view={x:cx-nw/2,y:cy-nh/2,w:nw,h:nh};updateView();}
  function focusCar(){const car=(current?.cars||[]).find(c=>c.id===selectedCar);if(!car||previewMap)return;followCar=true;const w=Math.min(fitView.w,100),h=w*fitView.h/fitView.w;view={x:number(car.x)-w/2,y:number(car.y)-h/2,w,h};updateView();}
  function mapPoint(event){const point=$("yard-map").createSVGPoint();point.x=event.clientX;point.y=event.clientY;const transform=$("yard-map").getScreenCTM();return transform?point.matrixTransform(transform.inverse()):null;}
  function renderAttribution(map){const host=$("map-attribution");empty(host);const layout=map.layout||{},scope=layout.scope||layout.disclaimer;if(scope)host.append(html("span",{},scope));const sources=layout.sources||map.sources||(layout.source?[{url:layout.source,title:`Audi Hungaria · hivatalos gyártérkép${layout.source_page?` (${layout.source_page}. oldal)`:""}`}]:[]);for(const source of sources){const url=typeof source==="string"?source:source.url;if(!url||!/^https?:\/\//.test(url))continue;host.append(html("a",{href:url,target:"_blank",rel:"noreferrer"},source.title||source.label||"Térképi forrás"));}}
  const scenarioFieldIds={car_count:"scenario-cars",parking_count:"scenario-parking",initial_occupied_count:"scenario-occupied",initial_car_count:"scenario-initial-cars",spawn_interval_s:"scenario-interval",parking_dwell_s:"scenario-dwell"};
  const copy=value=>JSON.parse(JSON.stringify(value));
  function scenarioToMap(config){const world=config.world||{},map=config.map||{};return {...config,...map,buildings:world.buildings||map.buildings||config.buildings,roads:world.roads||map.roads||config.roads,slots:config.slots||map.slots||[],crossings:config.crossings||map.crossings||[],zones:config.zones||map.zones||[],width:number(world.width||world.width_m||map.width||config.width,140),height:number(world.height||world.height_m||map.height||config.height,90),vehicle_length:config.vehicle?.length_m||config.vehicle?.length||config.vehicle_length||4.4,vehicle_width:config.vehicle?.width_m||config.vehicle?.width||config.vehicle_width||1.8};}
  function scenarioError(message=""){$("scenario-error").hidden=!message;$("scenario-error").textContent=message;$("scenario-apply").dataset.previewInvalid=message?"true":"false";refreshControls();}
  function fillScenarioFields(){if(!scenarioData||!scenarioDraft)return;const active=scenarioDraft,config=scenarioData.config||{};$("scenario-preset").replaceChildren();for(const preset of scenarioData.presets||[])$("scenario-preset").add(new Option(preset.name,preset.id));$("scenario-preset").value=active.preset||scenarioData.presets?.[0]?.id||"";$("scenario-preset").disabled=false;for(const [key,id]of Object.entries(scenarioFieldIds))$(id).value=active[key]??({car_count:6,parking_count:16,initial_occupied_count:4,initial_car_count:0,spawn_interval_s:9,parking_dwell_s:45}[key]);$("scenario-limit").value=active.production_limit??"";fillScenarioLocations(config);$("scenario-name").textContent=(scenarioData.presets||[]).find(p=>p.id===active.preset)?.name||config.layout?.name||"Gyárudvar";$("scenario-counts").textContent=`${active.car_count} aktív autó · ${active.parking_count} parkoló · ${active.initial_occupied_count} kezdetben foglalt`;$("scenario-description").textContent=(scenarioData.presets||[]).find(p=>p.id===active.preset)?.description||config.layout?.scope||"";$("scenario-occupied").max=active.parking_count;$("scenario-initial-cars").max=active.car_count;}
  function fillScenarioLocations(config){fillSelect($("scenario-exit"),(config.production_exits||config.map?.production_exits||[]).map(v=>({value:v.id,label:v.label||v.id})),"Alapértelmezett kijárat");fillSelect($("scenario-area"),(config.parking_areas||config.map?.parking_areas||[]).map(v=>({value:v.id,label:v.label||v.id})),"Alapértelmezett parkoló");if(scenarioDraft?.production_exit_id)$("scenario-exit").value=scenarioDraft.production_exit_id;if(scenarioDraft?.parking_area_id)$("scenario-area").value=scenarioDraft.parking_area_id;}
  async function loadScenario(force=false){try{const data=await getJSON("/api/scenario");if(scenarioData&&scenarioDirty&&!force)return;scenarioData=data;scenarioDraft=copy(data.active||{});scenarioDraft.initial_cars??=[];scenarioDirty=false;fillScenarioFields();$("scenario-pending").hidden=true;scenarioError();fillPlacementItems();}catch(error){$("scenario-name").textContent="A telephelybeállítások betöltésére várunk…";if(!scenarioData)setTimeout(()=>loadScenario(),5000);}}
  function readScenarioFields(){
    if(!scenarioDraft)return;
    const priorPreset=scenarioDraft.preset,priorExit=scenarioDraft.production_exit_id,priorArea=scenarioDraft.parking_area_id,priorParking=scenarioDraft.parking_count;
    scenarioDraft.preset=$("scenario-preset").value;
    for(const[key,id]of Object.entries(scenarioFieldIds))scenarioDraft[key]=number($(id).value,-1);
    scenarioDraft.production_limit=$("scenario-limit").value===""?null:number($("scenario-limit").value,-1);
    if($("scenario-exit").value)scenarioDraft.production_exit_id=$("scenario-exit").value;
    if($("scenario-area").value)scenarioDraft.parking_area_id=$("scenario-area").value;
    if(priorPreset!==scenarioDraft.preset||priorArea!==scenarioDraft.parking_area_id||priorParking!==scenarioDraft.parking_count)delete scenarioDraft.slots;
    if(priorPreset!==scenarioDraft.preset||priorExit!==scenarioDraft.production_exit_id)delete scenarioDraft.initial_cars;
    const count=scenarioDraft.initial_car_count;
    if(Array.isArray(scenarioDraft.initial_cars)&&Number.isInteger(count)&&count>=0&&count<=30&&scenarioDraft.initial_cars.length!==count){
      const initial=scenarioDraft.initial_cars.slice(0,count),config=scenarioData?.config||{},exits=config.production_exits||config.map?.production_exits||[];
      const exit=exits.find(e=>e.id===scenarioDraft.production_exit_id),positions=exit?.initial_car_positions||[];
      for(const position of positions){
        if(initial.length>=count)break;
        if(initial.some(car=>Math.hypot(number(car.x)-number(position.x),number(car.y)-number(position.y))<6.799))continue;
        initial.push(copy(position));
      }
      // If a preset has not been compiled yet, the backend generates its valid positions.
      if(initial.length===count)scenarioDraft.initial_cars=initial;else delete scenarioDraft.initial_cars;
    }
    $("scenario-occupied").max=scenarioDraft.parking_count;$("scenario-initial-cars").max=scenarioDraft.car_count;
  }
  function markScenarioDirty(){scenarioDirty=true;previewVersion++;$("scenario-pending").hidden=false;$("scenario-pending").textContent="Nem alkalmazott módosítások";}
  function validScenarioFields(){readScenarioFields();for(const id of [...Object.values(scenarioFieldIds),"scenario-limit"]){if(!$(id).checkValidity()){$(id).reportValidity();return false;}}return true;}
  async function previewScenario(){if(coordinateEdit){showToast("A módosított koordinátákat előbb mentsd a Pozíció mentése gombbal.",true);return;}if(!scenarioDraft||previewBusy||!validScenarioFields())return;readScenarioFields();const version=++previewVersion,request=copy(scenarioDraft);request.preview_seed=number($("seed-input").value,42);previewBusy=true;$("scenario-preview").disabled=true;$("scenario-preview").textContent="Előnézet készül…";try{const data=await getJSON(`/api/scenario?scenario=${encodeURIComponent(JSON.stringify(request))}`);if(version!==previewVersion)return;scenarioData={...data};scenarioDraft=copy(data.active||request);scenarioDraft.initial_cars??=[];previewMap=scenarioToMap(data.config||{});previewMap.initial_occupied=Array.isArray(data.preview_occupied_slots)?data.preview_occupied_slots:[];mapKey=null;buildMap(previewMap);$("map-preview-banner").hidden=false;$("map-data-source").classList.add("preview");$("map-data-source").replaceChildren(html("i"),document.createTextNode("TERVEZETT INDULÓ ÁLLAPOT"));$("map-wait").hidden=true;fillScenarioLocations(data.config||{});fillPlacementItems();scenarioError();$("scenario-pending").textContent="Előnézet · még nem fut";}catch(error){if(version===previewVersion)scenarioError(`Az előnézet nem készíthető el: ${error.message}. A koordinátákat és az elérhetőséget a szerver ellenőrzi.`);}finally{previewBusy=false;if(scenarioDirty&&version!==previewVersion&&!coordinateEdit)schedulePreview();$("scenario-preview").disabled=false;$("scenario-preview").textContent="Előnézet frissítése";}}
  function schedulePreview(){clearTimeout(previewTimer);previewTimer=setTimeout(previewScenario,450);}
  function placementConfig(){return previewMap||scenarioToMap(scenarioData?.config||{});}
  function placementPosition(){const mode=$("placement-mode").value,id=$("placement-item").value;if(mode==="slot")return (scenarioDraft?.slots||[]).find(s=>s.id===id)||(placementConfig().slots||[]).find(s=>s.id===id);if(mode==="car")return scenarioDraft?.initial_cars?.[Number(id)];return null;}
  function clearCoordinateEdit(){coordinateEdit=null;$("placement-hint").textContent=placementHelp;refreshControls();}
  function editCoordinates(){
    if(!placementPosition())return;
    coordinateEdit={mode:$("placement-mode").value,id:$("placement-item").value,x:$("placement-x").value,y:$("placement-y").value};
    // A response for an older draft may never overwrite a field being typed into.
    previewVersion++;clearTimeout(previewTimer);
    $("placement-hint").textContent="A koordináták még nincsenek mentve. A Pozíció mentése gomb rögzíti és ellenőrzi őket; ezután alkalmazható a forgatókönyv.";
    $("scenario-pending").hidden=false;$("scenario-pending").textContent="Nem mentett koordináták";refreshControls();
  }
  function fillPlacementItems(preferred=null){
    const mode=$("placement-mode").value,select=$("placement-item"),config=placementConfig();
    fillSelect(select,mode==="slot"?(config.slots||[]).map(s=>({value:s.id,label:`${s.id} · parkolóhely`})):mode==="car"?(scenarioDraft?.initial_cars||[]).map((c,i)=>({value:String(i),label:`Kezdeti autó ${i+1}`})):[],"Nincs kijelölt elem");
    if(preferred!==null&&[...select.options].some(o=>o.value===String(preferred)))select.value=String(preferred);
    if(coordinateEdit&&(coordinateEdit.mode!==mode||coordinateEdit.id!==select.value))clearCoordinateEdit();
    const item=placementPosition(),editing=coordinateEdit&&coordinateEdit.mode===mode&&coordinateEdit.id===select.value;
    for(const[id,key]of [["placement-x","x"],["placement-y","y"]]){
      $(id).value=editing?coordinateEdit[key]:item?number(item[key]).toFixed(1):"";$(id).disabled=!item;
    }
    $("placement-save").disabled=!item;$("initial-car-remove").disabled=mode!=="car"||!item;
    $("initial-car-add").disabled=!scenarioDraft||number(scenarioDraft.initial_cars?.length)>=number(scenarioDraft.car_count,6);
    $("editor-mode-badge").textContent=mode==="slot"?"Parkoló szerkesztése":mode==="car"?"Kezdeti autó szerkesztése":"Nézet";
    $("editor-mode-badge").className=`data-badge ${mode==="none"?"neutral":"warn"}`;
    $("yard-map").classList.toggle("placement-active",mode!=="none"&&Boolean(previewMap));renderEditorMap();
  }
  function savePlacement(x=null,y=null){const item=placementPosition(),mode=$("placement-mode").value;if(!item||!scenarioDraft)return;const nx=x===null?number($("placement-x").value,NaN):x,ny=y===null?number($("placement-y").value,NaN):y;if(!Number.isFinite(nx)||!Number.isFinite(ny)){scenarioError("Mindkét koordinátához számot adj meg.");return;}clearCoordinateEdit();if(mode==="slot"){scenarioDraft.slots??=[];const previous=scenarioDraft.slots.find(s=>s.id===item.id),update={id:item.id,x:Number(nx.toFixed(1)),y:Number(ny.toFixed(1))};if(previous)Object.assign(previous,update);else scenarioDraft.slots.push(update);}else Object.assign(item,{x:Number(nx.toFixed(1)),y:Number(ny.toFixed(1))});markScenarioDirty();fillPlacementItems($("placement-item").value);schedulePreview();}
  function renderEditorMap(){empty($("editor-layer"));if(!previewMap||!scenarioDraft)return;const layer=$("editor-layer"),scale=Math.max(1,fullMap.width/190);for(const slot of previewMap.slots||[]){const group=parkingNodes.get(slot.id);if(!group)continue;const occupied=Array.isArray(previewMap.initial_occupied)&&previewMap.initial_occupied.includes(slot.id);group.querySelector(".slot-outline").style.fill=occupied?"#e4eaf3":"#e2f2e9";group.querySelector(".slot-outline").style.stroke=occupied?"#8096b4":"#71ab91";group.classList.toggle("selected",$("placement-mode").value==="slot"&&$("placement-item").value===slot.id);}for(const[ i,car]of (scenarioDraft.initial_cars||[]).entries()){const g=node("g",{class:"initial-car-marker",role:"button",tabindex:0,"aria-label":`Kezdeti autó ${i+1}`,transform:`translate(${number(car.x)} ${number(car.y)})`},layer);node("circle",{cx:0,cy:0,r:3*scale,fill:"#8263b915",stroke:"#8263b9","stroke-width":.3*scale,"stroke-dasharray":`${scale} ${scale}`},g);node("rect",{x:-2.2,y:-.9,width:4.4,height:1.8,rx:.4,fill:"#8263b9",transform:`rotate(${number(car.heading)})`},g);node("text",{x:0,y:-4*scale,"text-anchor":"middle",class:"initial-car-label"},g,`K${i+1}`);node("title",{},g,`Kezdeti autó ${i+1}: ${fmt(car.x)}; ${fmt(car.y)} m`);const select=()=>{if(movedPointer)return;$("placement-mode").value="car";fillPlacementItems(String(i));};g.addEventListener("click",select);g.addEventListener("keydown",e=>{if(e.key==="Enter"||e.key===" "){e.preventDefault();select();}});}const selected=placementPosition();if(selected&&$("placement-mode").value!=="none"){node("circle",{cx:selected.x,cy:selected.y,r:6*scale,fill:"none",stroke:"#8360b9","stroke-width":.4*scale,"stroke-dasharray":`${scale} ${scale}`},layer);node("path",{d:`M${number(selected.x)-8*scale} ${selected.y}h${16*scale}M${selected.x} ${number(selected.y)-8*scale}v${16*scale}`,stroke:"#8360b9","stroke-width":.25*scale},layer);}}
  async function addInitialCar(){if(!scenarioDraft)return;if(!previewMap){await previewScenario();if(!previewMap)return;}const config=scenarioData.config||{},exits=config.production_exits||config.map?.production_exits||[],exit=exits.find(e=>e.id===scenarioDraft.production_exit_id)||exits[0],suggestions=config.initial_car_positions||exit?.initial_car_positions||exit?.initial_positions||[],index=scenarioDraft.initial_cars?.length||0;let position=suggestions[index];if(!position&&exit){const lane=exit.initial_lane||exit.spawn_lane;if(lane?.length>1){const a=lane[0],b=lane[1],length=Math.hypot(b[0]-a[0],b[1]-a[1]),distance=Math.min(length,index*9);position={x:a[0]+(b[0]-a[0])*distance/length,y:a[1]+(b[1]-a[1])*distance/length,heading:Math.atan2(b[1]-a[1],b[0]-a[0])*180/Math.PI};}else position={x:exit.spawn?.[0]??exit.x-index*9,y:exit.spawn?.[1]??exit.y,heading:0};}if(!position){scenarioError("A kiválasztott kijárathoz nincs megadott indulási pozíció.");return;}scenarioDraft.initial_cars??=[];scenarioDraft.initial_cars.push({x:number(position.x??position[0]),y:number(position.y??position[1]),heading:number(position.heading,180)});scenarioDraft.initial_car_count=scenarioDraft.initial_cars.length;$("scenario-initial-cars").value=scenarioDraft.initial_car_count;markScenarioDirty();$("placement-mode").value="car";fillPlacementItems(String(index));schedulePreview();}
  async function applyScenario(){if(coordinateEdit){showToast("A módosított koordinátákat előbb mentsd a Pozíció mentése gombbal.",true);return;}if(!scenarioDraft||!validScenarioFields())return;readScenarioFields();const result=await control("configure",{scenario:copy(scenarioDraft),seed:number($("seed-input").value,42),strategy:$("strategy-select").value},"A telephelybeállításokkal új futás indult");if(result){scenarioDirty=false;previewMap=null;mapKey=null;$("map-preview-banner").hidden=true;$("scenario-pending").hidden=true;$("placement-mode").value="none";$("map-data-source").classList.remove("preview");$("map-data-source").replaceChildren(html("i"),document.createTextNode("MQTT-n fogadott állapot"));await loadScenario(true);if(current?.map)render(current);}}
  async function discardScenario(){clearCoordinateEdit();previewVersion++;clearTimeout(previewTimer);scenarioDirty=false;previewMap=null;mapKey=null;followCar=false;$("placement-mode").value="none";$("map-preview-banner").hidden=true;$("map-data-source").classList.remove("preview");$("map-data-source").replaceChildren(html("i"),document.createTextNode("MQTT-n fogadott állapot"));await loadScenario(true);if(current?.map)render(current);}
  function downloadScenario(event){
    if(!scenarioDraft){showToast("A futó forgatókönyv letöltése elindítva.");return;}
    if(coordinateEdit||!validScenarioFields()){
      event.preventDefault();
      if(coordinateEdit)showToast("A módosított koordinátákat előbb mentsd a Pozíció mentése gombbal.",true);
      return;
    }
    const seed=number($("seed-input").value,-1);
    if(!Number.isInteger(seed)||seed<0||seed>2147483647){event.preventDefault();showToast("A véletlenmag 0 és 2147483647 közötti egész szám legyen.",true);return;}
    const params=copy(scenarioDraft);params.preview_seed=seed;
    $("scenario-download").href=`/api/scenario/export?scenario=${encodeURIComponent(JSON.stringify(params))}&strategy=${encodeURIComponent($("strategy-select").value)}`;
    // Keep the native HTTP download so embedded browsers can expose the file.
    showToast("A forgatókönyv letöltése elindítva.");
  }
  async function importScenario(event){clearCoordinateEdit();const file=event.target.files?.[0];if(!file)return;try{if(file.size>65536)throw new Error("A forgatókönyvfájl legfeljebb 64 KiB lehet");const documentData=JSON.parse(await file.text()),params=documentData.scenario||documentData.active||documentData;if(!params||typeof params!=="object"||Array.isArray(params))throw new Error("A fájl forgatókönyv-objektumot tartalmazzon");if(documentData.schema_version!==undefined&&documentData.schema_version!==1)throw new Error("Nem támogatott forgatókönyv-verzió");scenarioDraft=copy(params);if(Number.isInteger(documentData.seed)&&documentData.seed>=0&&documentData.seed<=2147483647)$("seed-input").value=documentData.seed;if(["nearest","fixed"].includes(documentData.strategy))$("strategy-select").value=documentData.strategy;fillScenarioFields();markScenarioDirty();$("editor-body").hidden=false;$("editor-toggle").setAttribute("aria-expanded","true");await previewScenario();showToast("A fájl előnézetként betöltve. Az alkalmazás gomb indít új futást.");}catch(error){scenarioError(`A fájl nem tölthető be: ${error.message}`);}finally{event.target.value="";}}
  function renderParking() {
    if(!current)return;const slots=current.parking||[];let free=0,reserved=0,occupied=0,unknown=0;for(const slot of slots){const state=parkingState(slot);if(state==="free")free++;if(state==="reserved")reserved++;if(state==="occupied")occupied++;if(state==="unknown")unknown++;const g=previewMap?null:parkingNodes.get(slot.id);if(!g)continue;const [fill,stroke]=parkingColors[state]||parkingColors.unknown;g.querySelector(".slot-outline").setAttribute("fill",fill);g.querySelector(".slot-outline").style.fill=fill;g.querySelector(".slot-outline").style.stroke=stroke;g.classList.toggle("selected",slot.id===selectedSlot);const symbol=g.querySelector(".slot-symbol");symbol.textContent=state==="unknown"?"?":state==="blocked"?"×":state==="reserved"?"⌁":state==="occupied"?"•":"P";symbol.style.fill=stroke;g.querySelector("title").textContent=`${slot.id}: ${{free:"szabad",reserved:"lefoglalt",occupied:"foglalt",blocked:"lezárt",unknown:"ismeretlen, friss adat hiányzik"}[state]||state}${slot.car_id?` · ${slot.car_id}`:""}`;g.setAttribute("aria-label",g.querySelector("title").textContent);}
    const total=slots.length||current.map?.slots?.length||16;$("metric-free").replaceChildren(document.createTextNode(`${unknown===slots.length||!slots.length?"—":free} `),Object.assign(document.createElement("small"),{textContent:`/ ${total}`}));$("parking-summary").textContent=unknown||!slots.length?"Friss foglaltságadat hiányzik":`${reserved} lefoglalt · ${occupied} fizikailag foglalt`;
    const selected=slots.find(s=>s.id===$("slot-select").value);$("block-button").textContent=selected&&parkingState(selected)==="blocked"?"Feloldás":"Lezárás";
  }
  function drawMapCar(parent,id,length,width,shadowId="car-shadow"){
    const l=number(length,4.5),w=number(width,1.8),body=node("g",{class:"car-body",style:`filter:url(#${shadowId})`},parent);
    const selected=node("rect",{x:-l/2-.5,y:-w/2-.5,width:l+1,height:w+1,rx:.9,class:"car-selection",visibility:"hidden"},body);
    const shape=node("rect",{x:-l/2,y:-w/2,width:l,height:w,rx:.55,fill:colorFor(id),stroke:"#ffffff","stroke-width":.16},body);
    node("rect",{x:.4,y:-w/2+.18,width:.6,height:w-.36,rx:.1,fill:"#b6d9e7",opacity:.9},body);
    node("rect",{x:-1.1,y:-w/2+.2,width:.5,height:w-.4,rx:.1,fill:"#143b56",opacity:.55},body);
    node("path",{d:`M${l/2-.2},${-w/2+.25}v.35M${l/2-.2},${w/2-.6}v.35`,stroke:"#fff3bb","stroke-width":.3},body);
    return {body,shape,selected};
  }
  function ensureCar(car) {
    let entry=carNodes.get(car.id);if(entry)return entry;
    const g=node("g",{class:"car-group",role:"button",tabindex:0},$("cars-layer")),parts=drawMapCar(g,car.id,current.map?.vehicle_length,current.map?.vehicle_width);
    const label=node("text",{x:0,y:-number(current.map?.vehicle_width,1.8)/2-1,class:"car-id"},g,car.id),title=node("title",{},g,car.id);
    entry={g,...parts,label,title,x:number(car.x),y:number(car.y),heading:number(car.heading),targetX:number(car.x),targetY:number(car.y),targetHeading:number(car.heading)};carNodes.set(car.id,entry);
    const select=()=>selectCar(car.id);g.addEventListener("click",select);g.addEventListener("keydown",e=>{if(e.key==="Enter"||e.key===" "){e.preventDefault();select();}});return entry;
  }
  function renderCars() {
    if(previewMap)return;
    const cars=current?.cars||[];const ids=new Set(cars.map(c=>c.id));for(const [id,e]of carNodes)if(!ids.has(id)){e.g.remove();carNodes.delete(id);}empty($("routes-layer"));
    for(const car of cars){const e=ensureCar(car);e.targetX=number(car.x);e.targetY=number(car.y);e.targetHeading=number(car.heading);e.g.classList.toggle("is-selected",selectedCar===car.id);e.g.classList.toggle("is-muted",Boolean(selectedCar)&&selectedCar!==car.id);e.selected.setAttribute("visibility",selectedCar===car.id?"visible":"hidden");const stopped=["communication_stop","sensor_stop","frozen"].includes(car.state);e.shape.setAttribute("fill",stopped?"#c56c60":car.state==="pedestrian_stop"?"#d3993f":colorFor(car.id));e.title.textContent=`${car.id}: ${stateNames[car.state]||car.state}, ${fmt(car.speed)} m/s${car.slot_id?`, ${car.slot_id}`:""}`;e.g.setAttribute("aria-label",e.title.textContent);if($("show-routes").checked&&Array.isArray(car.route)&&car.route.length>1&&car.state!=="parked"){node("polyline",{points:car.route.map(p=>`${p[0]},${p[1]}`).join(" "),stroke:colorFor(car.id),class:`route-line ${selectedCar===car.id?"selected":""}`},$("routes-layer"));}}
  }
  function renderPedestrians() {if(previewMap)return;empty($("pedestrians-layer"));const active=new Set();for(const ped of current?.pedestrians||[]){active.add(ped.crossing_id);const g=node("g",{transform:`translate(${number(ped.x)} ${number(ped.y)})`},$("pedestrians-layer"));node("ellipse",{cx:0,cy:.45,rx:.6,ry:.8,fill:"#b67048",class:"pedestrian-outline"},g);node("circle",{cx:0,cy:-.35,r:.4,fill:"#4e4851",class:"pedestrian-outline"},g);node("title",{},g,`Gyalogos · ${ped.crossing_id} · ${ped.state}`);}for(const[id,e]of crossingNodes)e.setAttribute("class",active.has(id)?"crossing-active":"");}
  function selectedSensors(){
    if(previewMap)return null;
    const car=(current?.cars||[]).find(c=>c.id===selectedCar),sample=car?.sensors;
    if(!sample||sample.schema_version!==1||sample.source!=="virtual_onboard_bundle"||!sample.sampled||!hasValue(sample.pose?.x_m)||!hasValue(sample.pose?.y_m)||!hasValue(sample.reference_pose?.x_m)||!hasValue(sample.reference_pose?.y_m))return null;
    return {car,sample,fresh:Boolean(sample.fresh&&!sample.fault_active&&serverReachable&&current?.connected)};
  }
  // Both live and replay views use the received bundle. They do not read live
  // world geometry or pedestrian positions to invent sensor observations.
  const perceptionFrames=new Map();
  const proximitySelections=new Map();
  function selectedProximityChannel(prefix,data=perceptionFrames.get(prefix)){
    const selection=proximitySelections.get(prefix);
    if(!selection||!data||selection.runId!==data.run_id||selection.carId!==data.car_id)return null;
    return window.YardPerception.proximityChannels(data.sensors).find(channel=>channel.id===selection.channelId)||null;
  }
  function selectProximityChannel(prefix,channelId){
    const data=perceptionFrames.get(prefix),channel=window.YardPerception.proximityChannels(data?.sensors).find(c=>c.id===channelId);
    if(channel&&data)proximitySelections.set(prefix,{runId:data.run_id,carId:data.car_id,channelId:channel.id});
    else proximitySelections.delete(prefix);
    renderPerception(prefix,data);
    if(prefix==="live")renderSensorOverlay();else renderTripSensorOverlay(trip?.samples[trip.index],Boolean(trip?.perceptionGap));
  }
  function renderProximitySelection(prefix,data){
    const host=$(`${prefix}-proximity-selection`),channel=selectedProximityChannel(prefix,data);host.hidden=!channel;
    if(!channel)return;
    const visual=window.YardPerception.channelVisual(channel,data.fresh,data.sensors.proximity.thresholds_m),mount=channel.origin_world_m,hit=channel.hit_world_m;
    $(`${prefix}-proximity-selected-title`).textContent=`${channel.label||channel.id} · ${visual.observed?reading(visual.distance,"m",2):"nincs visszatérés"}`;
    const coordinates=p=>hasValue(p?.x_m)&&hasValue(p?.y_m)?`X ${fmt(p.x_m,2)}; Y ${fmt(p.y_m,2)} m`:"nincs mentett koordináta";
    $(`${prefix}-proximity-selected-detail`).textContent=`Minta ${reading(data.sensors.sample_sim_time_s,"s",2)} · ${data.fresh?"friss":"elavult / megtartott"}. Szerelési pont: ${coordinates(mount)}. ${visual.observed?`Első felület: ${channel.target?.label||channel.target?.id||"ismeretlen"} · ${coordinates(hit)}.`:"A vizsgált sugarakon nincs találat; a környezet szabad állapota nem igazolt."} A cián körvonal a kijelölést jelöli.`;
    $(`${prefix}-proximity-focus`).disabled=!hasValue(mount?.x_m)||!hasValue(mount?.y_m);
    $(`${prefix}-proximity-focus`).textContent=visual.observed&&hasValue(hit?.x_m)&&hasValue(hit?.y_m)?"Találat a térképen":"Nyaláb a térképen";
  }
  function focusProximityChannel(prefix){
    const data=perceptionFrames.get(prefix),channel=selectedProximityChannel(prefix,data),mount=channel?.origin_world_m;
    if(!hasValue(mount?.x_m)||!hasValue(mount?.y_m))return;
    const hit=channel.hit_world_m,visual=window.YardPerception.channelVisual(channel,data.fresh,data.sensors.proximity.thresholds_m),hasHit=visual.observed&&hasValue(hit?.x_m)&&hasValue(hit?.y_m),x=(Number(mount.x_m)+(hasHit?Number(hit.x_m):Number(mount.x_m)))/2,y=(Number(mount.y_m)+(hasHit?Number(hit.y_m):Number(mount.y_m)))/2;
    const bounds=prefix==="live"?fitView:trip?.bounds;if(!bounds)return;
    const aspect=bounds.h/bounds.w,w=Math.max(18,hasHit?Math.abs(hit.x_m-mount.x_m)+10:18,hasHit?(Math.abs(hit.y_m-mount.y_m)+10)/aspect:18),h=w*aspect;
    if(prefix==="live"){followCar=false;$("show-sensors").checked=true;view={x:x-w/2,y:y-h/2,w,h};updateView();$("yard-area").scrollIntoView({behavior:"smooth",block:"start"});}
    else{trip.follow=false;trip.view={x:x-w/2,y:y-h/2,w,h};updateTripView();$("trip-map-title").scrollIntoView({behavior:"smooth",block:"start"});}
  }
  const pedestrianLabels={waiting:"gyalogos vár",crossing:"gyalogos átkel",no_pedestrian_observed:"gyalogos nincs észlelve"};
  function usableSensorFrame(sample){return Boolean(sample?.schema_version===1&&sample.source==="virtual_onboard_bundle"&&sample.sampled&&hasValue(sample.reference_pose?.x_m)&&hasValue(sample.reference_pose?.y_m)&&hasValue(sample.pose?.heading_deg));}
  function sensorRelative(sample,observation){
    if(!hasValue(observation?.x_m)||!hasValue(observation?.y_m))return null;
    const origin=sample.reference_pose,angle=number(sample.pose.heading_deg)*Math.PI/180,dx=Number(observation.x_m)-Number(origin.x_m),dy=Number(observation.y_m)-Number(origin.y_m);
    const forward=dx*Math.cos(angle)+dy*Math.sin(angle),right=-dx*Math.sin(angle)+dy*Math.cos(angle);
    return {forward,right,distance:Math.hypot(dx,dy),bearing:Math.atan2(right,forward)*180/Math.PI};
  }
  function drawRadar(svg,sample,fresh,carId,options){
    return window.YardPerception.drawRadar(svg,sample,fresh,carId,options);
  }
  function drawFrontView(svg,sample,fresh,carId,options){
    return window.YardPerception.drawFrontView(svg,sample,fresh,carId,options);
  }
  function renderProximityTable(prefix,sample,fresh){
    const channels=window.YardPerception.proximityChannels(sample),rows=$(`${prefix}-proximity-rows`),table=$(`${prefix}-proximity-table`);
    const reuse=rows.children.length===channels.length&&channels.every((channel,index)=>rows.children[index].getAttribute("data-channel")===channel.id);
    if(!reuse)empty(rows);table.hidden=!channels.length;
    $(`${prefix}-proximity-summary`).textContent=channels.length?`${channels.length} mentett csatorna · ${reading(sample.proximity.sample_sim_time_s,"s",2)} mintavétel · ${fresh?"friss észlelés":"elavult / megtartott észlelés"}`:"Ehhez a mintához nincs rövid hatótávú közelségcsatorna.";
    for(const [index,channel]of channels.entries()){
      const visual=window.YardPerception.channelVisual(channel,fresh,sample.proximity.thresholds_m),row=reuse?rows.children[index]:html("tr",{"data-channel":channel.id});row.setAttribute("class",`proximity-row ${visual.status}`);
      const chosen=selectedProximityChannel(prefix)?.id===channel.id;row.classList.toggle("is-selected",chosen);
      const heading=reuse?row.children[0]:html("th",{scope:"row"}),label=reuse?heading.children[0]:html("button",{class:"proximity-channel-label proximity-channel-button",type:"button"}),dot=reuse?label.children[0]:html("i",{class:"proximity-status-dot","aria-hidden":"true"});dot.style.background=visual.color;label.setAttribute("aria-pressed",String(chosen));heading.title=channel.id;
      if(reuse)label.children[1].textContent=channel.label||channel.id;
      else{label.append(dot,html("span",{},channel.label||channel.id));label.addEventListener("click",()=>selectProximityChannel(prefix,selectedProximityChannel(prefix)?.id===channel.id?null:channel.id));heading.append(label);}
      const state=visual.status==="stale"?(visual.observed?"Elavult találat":"Elavult · nincs visszatérés"):{close:"Közeli",warning:"Figyelmeztetés",clear:"Távolabbi találat",no_return:"Nincs visszatérés"}[visual.status];
      const target=visual.observed?(channel.target?.label||channel.target?.id||"Ismeretlen testfelület"):"—",targetCell=reuse?row.children[3]:html("td",{class:"proximity-target"},target);targetCell.title=channel.target?.id||"";
      if(reuse){row.children[1].textContent=visual.observed?fmt(visual.distance,2):"—";row.children[2].textContent=state;targetCell.textContent=target;}
      else{row.append(heading,html("td",{class:"proximity-distance"},visual.observed?fmt(visual.distance,2):"—"),html("td",{class:"proximity-state"},state),targetCell);rows.append(row);}
    }
  }
  function perceptionSourceNote(sample){
    const channels=window.YardPerception.proximityChannels(sample),proximity=sample?.proximity;
    const proximityText=channels.length?`A ${reading(proximity.range_m,"m",2)} hatótávú közelségcsatornák a lökhárítótól mért első geometriai találatot mutatják. Piros: legfeljebb ${reading(proximity.thresholds_m?.close,"m",2)}; sárga: legfeljebb ${reading(proximity.thresholds_m?.warning,"m",2)}; zöld: távolabbi találat; szürke: nincs visszatérés. Csatornánként ${hasValue(proximity.rays_per_channel)?proximity.rays_per_channel:"ismeretlen számú"} tesztelt sugár, ±${reading(proximity.beam_half_angle_deg,"°",1)} nyaláb. A nyalábok között és hatótávon túl a találat hiánya nem igazol szabad területet. A közelségi térkép és az elölnézet a mentett geometriai mintavételi helyet és irányt használja; a lokalizációs zaj nem fordítja el a találati pontokat.`:"Régi, közelségcsatorna nélküli mintából nem feltételezünk új mérést; az elölnézet a régi mért irány szerinti vetületet használja.";
    return `${proximityText} A színküszöbök a mintához tartozó modellbeállítások. A ${reading(sample?.range_m,"m",1)} környezeti mintában nincs takarásmodell; a fogadott tárgykoordináták zaját megtartjuk. A zebra térképi állapot, az elölnézet nem kamerakép. A felületi pontokból nem rekonstruáljuk az akadály teljes alakját. A JSON-export megőrzi a mintát.`;
  }
  function renderBrakingDecision(prefix,data){
    const result=window.YardBrakingAnalysis.analyzeFrame(data),panel=$(`${prefix}-braking-panel`),badge=$(`${prefix}-braking-status`);
    const phases={braking:"Fékezés folyamatban",accelerating:"Gyorsítás",cruising:"Egyenletes haladás",stopped:"Megállt"};
    const retained=result.freshness!=="current",inconsistent=result.consistency==="inconsistent";
    panel.dataset.reserve=result.model_reserve_m===null?"unknown":result.model_reserve_m<0?"negative":"nonnegative";
    panel.dataset.state=!result.available?"unknown":inconsistent?"inconsistent":retained?"retained":result.phase;
    badge.textContent=!result.available?"Nincs értelmezhető döntés":inconsistent?"Eltérő diagnosztikai adatok":retained?"Megőrzött döntés":"Fogadott döntés";
    badge.className=`data-badge ${!result.available?"neutral":inconsistent||retained?"warn":"good"}`;
    $(`${prefix}-braking-phase`).textContent=result.available?phases[result.phase]||"Ismeretlen mozgás":"Nem állapítható meg";
    $(`${prefix}-braking-speed`).textContent=result.available?`${reading(result.speed_before_m_s,"m/s",2)} → ${reading(result.speed_after_m_s,"m/s",2)}`:"—";
    $(`${prefix}-braking-acceleration`).textContent=result.available?reading(result.applied_acceleration_m_s2,"m/s²",2):"—";
    $(`${prefix}-braking-limit`).textContent=result.available?reading(result.stop_limit_remaining_m,"m",2):"—";
    $(`${prefix}-braking-reserve`).textContent=result.available&&result.consistency==="consistent"?reading(result.model_reserve_m,"m",2):"—";
    $(`${prefix}-braking-use`).textContent=result.available&&result.consistency==="consistent"?reading(result.braking_utilization_pct,"%",0):"—";
    $(`${prefix}-braking-clock`).textContent=result.available?`Döntés: ${reading(result.decision_sim_time_s,"s",2)} · lépés: ${reading(result.motion_interval_s,"s",3)}${data?.gap?" · visszajátszási adatrés":retained?" · megtartott megfigyelés":""}`:"A naplózott vezérlési döntésre várunk.";
    const bar=$(`${prefix}-braking-bar`),remaining=result.stop_limit_remaining_m,brake=result.braking_distance_m;
    const comparable=result.available&&result.consistency==="consistent"&&remaining!==null&&brake!==null&&(remaining>0||brake>0);
    bar.hidden=!comparable;
    if(comparable){
      const span=Math.max(remaining,brake),used=Math.min(100,100*brake/span),limit=Math.min(100,100*remaining/span);
      bar.setAttribute("aria-label",`Ideális fékút: ${reading(brake,"m",2)}; megállási célig: ${reading(remaining,"m",2)}. Modellértékek.`);
      $(`${prefix}-braking-used`).style.width=`${used}%`;$(`${prefix}-braking-marker`).style.left=`${limit}%`;
    }else bar.removeAttribute("aria-label");
    $(`${prefix}-braking-note`).textContent=!result.available?"A hiányzó, régi vagy hibás döntési adatokból nem számolunk megállási tartalékot.":inconsistent?"A naplózott időpontok vagy kinematikai értékek nem illeszkednek; származtatott tartalék és kihasználás nem jelenik meg.":`${retained?"A megőrzött döntés számai láthatók; új vagy élő észlelést nem jelentenek. ":""}A tartalék a mozgás előtti megállási céltávolság és az ideális fékút különbsége. A cél már tartalmaz ráhagyásokat; a reakcióutat nem vonjuk le még egyszer. Ez modellbeli összevetés, nem igazolt fizikai biztonsági távolság.`;
  }
  function renderPerception(prefix,data){
    renderBrakingDecision(prefix,data);
    const available=usableSensorFrame(data?.sensors);perceptionFrames.set(prefix,available?data:null);renderProximitySelection(prefix,available?data:null);
    for(const kind of ["svg","json"]){const anchor=$(`${prefix}-perception-${kind}`);anchor.setAttribute("aria-disabled",String(!available));anchor.classList.toggle("disabled",!available);if(!available)anchor.removeAttribute("href");else anchor.href="#";}
    if(!available){empty($(`${prefix}-radar`));empty($(`${prefix}-front-view`));renderProximityTable(prefix,null,false);$(`${prefix}-perception-source`).textContent="Az észlelési magyarázat a fogadott vagy mentett mintával együtt jelenik meg.";$(`${prefix}-perception`).classList.remove("is-stale");delete $(`${prefix}-perception`).dataset.carId;delete $(`${prefix}-perception`).dataset.sampleTime;return;}
    const sample=data.sensors,fresh=Boolean(data.fresh),control=data.control||{},vehicles=(sample.environment?.vehicles||[]).filter(v=>hasValue(v.distance_m)).sort((a,b)=>Number(a.distance_m)-Number(b.distance_m)),crossings=sample.environment?.crossings||[],nearest=vehicles[0],channels=window.YardPerception.proximityChannels(sample),returns=channels.filter(c=>window.YardPerception.channelVisual(c,fresh,sample.proximity.thresholds_m).observed).sort((a,b)=>Number(a.distance_m)-Number(b.distance_m)),nearestSurface=returns[0];
    $(`${prefix}-perception`).classList.toggle("is-stale",!fresh);$(`${prefix}-perception`).dataset.carId=data.car_id;$(`${prefix}-perception`).dataset.sampleTime=sample.sample_sim_time_s;
    $(`${prefix}-perception-meta`).textContent=`${data.car_id} · érzékelés ${reading(sample.sample_sim_time_s,"s",2)} · ${data.telemetry_source==="simulation_controller_diagnostics"?"modelldiagnosztika":data.telemetry_source==="snapshot"?"MQTT-állapotkép":"telemetria"} ${reading(data.telemetry_sim_time,"s",2)} · kor ${reading(sample.age_s,"s",2)}${hasValue(control.decision_sim_time_s)?` · döntés ${reading(control.decision_sim_time_s,"s",2)}`:""}${fresh?"":" · utolsó ismert megfigyelés"}`;
    const options={selectedChannel:selectedProximityChannel(prefix,data)?.id};drawRadar($(`${prefix}-radar`),sample,fresh,data.car_id,options);const frontal=drawFrontView($(`${prefix}-front-view`),sample,fresh,data.car_id,options);renderProximityTable(prefix,sample,fresh);$(`${prefix}-perception-source`).textContent=perceptionSourceNote(sample);
    $(`${prefix}-radar-caption`).textContent=channels.length?`${channels.length} virtuális parkolószenzor · ${reading(sample.proximity.range_m,"m")} hatótáv · ${returns.length} felületi találat. Piros: ≤ ${fmt(sample.proximity.thresholds_m?.close,2)} m; sárga: ≤ ${fmt(sample.proximity.thresholds_m?.warning,2)} m; zöld: távolabbi találat; szürke: nincs visszajelzés.${fresh?"":" Elavult minta: a színes állapotjelzés szünetel."}`:`Régebbi minta: ${reading(sample.range_m,"m")} környezeti áttekintés · ${vehicles.length} észlelt autó · ${crossings.length} térképi zebraállapot. Rövid hatótávú parkolószenzor-adat nem áll rendelkezésre.`;
    $(`${prefix}-front-caption`).textContent=`${frontal} megfigyelés az előretekintő 90°-os vetületben. A segédsík nem felismert útburkolat; a jelképek mérete szemléltető.${crossings.some(c=>c.pedestrian_state==="crossing")?" A zebra jelölése az átkelési állapotot mutatja, gyalogos helyét nem." :""}`;
    const relative=nearest?sensorRelative(sample,nearest):null;
    $(`${prefix}-perception-nearest`).textContent=channels.length?(nearestSurface?`${nearestSurface.label} · ${reading(nearestSurface.distance_m,"m")} felületi távolság · ${nearestSurface.target?.id||"akadály"}`:"Nincs felületi találat a tesztelt nyalábokban"):nearest?`${nearest.id} · ${reading(nearest.distance_m,"m")} középponttávolság${relative?` · ${fmt(relative.bearing,1)}°`:""}`:"Nincs másik autó a mintában";
    $(`${prefix}-perception-target`).textContent=reading(control.target_speed_m_s,"m/s");$(`${prefix}-perception-braking`).textContent=`${reading(control.braking_distance_m,"m")} · ${reading(control.reaction_allowance_m,"m")}`;
    $(`${prefix}-perception-braking`).title="Ideális járműmodellből számított fékút és reakcióráhagyás; nem minősített biztonsági távolság.";
    $(`${prefix}-perception-decision`).textContent=control.source?control.stop_reason||stateNames[control.target_state]||"Normál haladási döntés":"Ehhez a mintához nincs mentett vezérlési döntés";
  }
  function renderTripPerception(point,gap=false){
    const available=usableSensorFrame(point?.sensors),sample=point?.sensors;
    $("trip-perception-content").hidden=!available;$("trip-perception-empty").hidden=available;
    $("trip-perception-status").textContent=!available?(sample?.sampled===false?"Első mintavételre vár":"Nincs mentett érzékelőminta"):gap?"Adathiány · utolsó minta":sample.fault_active?"Mentett érzékelőhiba":sample.fresh?"Mentett friss minta":"Mentett elavult minta";
    $("trip-perception-status").className=`data-badge ${available?(gap||sample.fault_active||!sample.fresh?"warn":"good"):"neutral"}`;
    if(!available)$("trip-perception-meta").textContent=`${trip?.carId||"Jármű"} · ${point?reading(point.sim_time):"nincs fogadott minta"} · az észlelések ebből az archívumból nem állíthatók vissza.`;
    if(!available)$("trip-perception-empty").textContent=sample?.sampled===false?"A fogadott csomag még nem tartalmazott sikeres virtuális érzékelőmintát. Az első mintavétel előtti megfigyeléseket nem feltételezzük.":sample?"A mentett érzékelőcsomag hiányos vagy más verziójú; ebből az egocentrikus nézet nem rajzolható meg.":"Ehhez a fogadott mintához nincs mentett érzékelőcsomag. A korábbi futásokból hiányzó megfigyelések nem rekonstruálhatók.";
    renderPerception("trip",{sensors:sample,control:point?.control||null,car_id:trip?.carId,run_id:trip?.runId,telemetry_sim_time:point?.sim_time,telemetry_source:point?.details?.source||null,utc:point?.utc||null,fresh:Boolean(sample?.fresh&&!sample?.fault_active&&!gap),archive:true,gap});
  }
  function preparePerceptionDownload(event,prefix,kind){
    event.preventDefault();const data=perceptionFrames.get(prefix);if(!data){showToast("Ehhez a mintához nincs mentett érzékelőcsomag.",true);return;}
    const documentData={schema_version:1,kind:"received_virtual_sensor_frame",run_id:data.run_id,car_id:data.car_id,telemetry_sim_time:data.telemetry_sim_time,telemetry_source:data.telemetry_source,utc:data.utc,sensors:data.sensors,control:data.control,view_context:{archived:Boolean(data.archive),held_during_gap:Boolean(data.gap),fresh_in_view:Boolean(data.fresh),selected_channel_id:selectedProximityChannel(prefix,data)?.id||null,description:"Egocentrikus virtuális radarnézet és szintetikus elölnézet; nem kamera- vagy radarfelvétel."}};
    documentData.braking_analysis=window.YardBrakingAnalysis.analyzeFrame(data);
    let content;if(kind==="json"){content=JSON.stringify(documentData,null,2);}
    else{const svg=node("svg",{xmlns:NS,width:910,height:397,viewBox:"0 0 910 397","font-family":"Segoe UI, Arial, sans-serif"});node("rect",{width:910,height:397,fill:"#f4f9fa"},svg);node("text",{x:16,y:23,fill:"#3d7183","font-size":14,"font-weight":600},svg,`${data.car_id} · minta ${fmt(data.sensors.sample_sim_time_s,2)} s · ${data.run_id}`);const radar=$(`${prefix}-radar`).cloneNode(true),front=$(`${prefix}-front-view`).cloneNode(true);for(const s of [radar,front])s.removeAttribute("id");radar.setAttribute("x",10);radar.setAttribute("y",37);radar.setAttribute("width",340);radar.setAttribute("height",340);front.setAttribute("x",372);front.setAttribute("y",37);front.setAttribute("width",510);front.setAttribute("height",300);svg.append(radar,front);node("text",{x:372,y:359,fill:"#64828e","font-size":11},svg,"Fogadott virtuális észlelésekből rajzolva; nem kamerafelvétel.");node("text",{x:372,y:378,fill:"#64828e","font-size":11},svg,"Zebra: ismert térképi helyhez társított állapot, nem gyalogos-pozíció.");node("metadata",{},svg,JSON.stringify(documentData));content=`<?xml version="1.0" encoding="UTF-8"?>\n${new XMLSerializer().serializeToString(svg)}`;}
    // A native same-origin HTTP attachment also works in embedded browsers
    // that cannot deliver Blob downloads. The server echoes these bytes and
    // does not store a transient client frame or maintain another renderer.
    const filename=`${data.car_id}_${String(data.run_id||"run").replace(/[^a-zA-Z0-9_-]/g,"")}_${number(data.telemetry_sim_time).toFixed(3)}_erzekelo.${kind}`;
    const fields={filename,format:kind,content},normalized=Object.fromEntries(Object.entries(fields).map(([name,value])=>[name,String(value).replace(/\r\n|\r|\n/g,"\r\n")])),encoder=new TextEncoder();
    if(encoder.encode(normalized.content).length>128*1024||encoder.encode(new URLSearchParams(normalized).toString()).length>256*1024){showToast("Ez a minta meghaladja az állókép-export méretkorlátját. A teljes út JSON-exportjával töltsd le az adatokat.",true);return;}
    const form=document.createElement("form");form.method="post";form.action="/api/perception/export";form.enctype="application/x-www-form-urlencoded";form.acceptCharset="UTF-8";form.hidden=true;
    for(const [name,value]of Object.entries(fields)){const input=document.createElement("input");input.type="hidden";input.name=name;input.value=value;form.append(input);}
    document.body.append(form);try{form.submit();showToast(`${kind==="svg"?"Az állókép":"Az érzékelőminta"} letöltése elindítva.`);}catch(error){showToast(`A letöltés nem indítható: ${error.message}`,true);}finally{setTimeout(()=>form.remove(),1000);}
  }
  function drawMapSensorOverlay(layer,data,pixel){
    const {car,sample,fresh}=data,origin=sample.reference_pose,pose=sample.pose,guidance=sample.guidance||{},environment=sample.environment||{};
    layer.classList.toggle("sensor-layer-stale",!fresh);layer.dataset.carId=car.id;layer.dataset.sampleTime=sample.sample_sim_time_s;layer.dataset.fresh=String(fresh);
    const ring=node("circle",{cx:origin.x_m,cy:origin.y_m,r:sample.range_m,class:"sensor-range-ring"},layer);
    node("title",{},ring,`${car.id} · ${fresh?"friss":"utolsó ismert"} virtuális minta · körkörös ${fmt(sample.range_m)} m hatótáv · takarásmodell nélkül`);
    if(guidance.available&&hasValue(guidance.projected_x_m)&&hasValue(guidance.projected_y_m)){
      const angle=number(guidance.route_heading_deg)*Math.PI/180,x=number(guidance.projected_x_m),y=number(guidance.projected_y_m);
      node("line",{x1:x-4*Math.cos(angle),y1:y-4*Math.sin(angle),x2:x+8*Math.cos(angle),y2:y+8*Math.sin(angle),class:"sensor-reference-line"},layer);
      const projection=node("circle",{cx:x,cy:y,r:2.8*pixel,class:"sensor-reference-point"},layer);node("title",{},projection,`Zajos helybecslés vetülete az ismert útvonalra · oldaleltérés ${fmt(guidance.lateral_error_m,3)} m`);
      node("line",{x1:pose.x_m,y1:pose.y_m,x2:x,y2:y,class:"sensor-reference-line"},layer);
    }
    const bound=Math.max(0,number(sample.position_uncertainty_m));
    const uncertainty=node("rect",{x:number(pose.x_m)-bound,y:number(pose.y_m)-bound,width:2*bound,height:2*bound,class:"sensor-pose-bound"},layer);
    node("title",{},uncertainty,`Méretarányos pozícióhibakorlát: ±${fmt(bound,2)} m külön-külön az X és Y tengely mentén`);
    const mark=4*pixel;node("path",{d:`M${number(pose.x_m)-mark} ${pose.y_m}h${2*mark}M${pose.x_m} ${number(pose.y_m)-mark}v${2*mark}`,class:"sensor-pose-mark"},layer);
    for(const detected of environment.vehicles||[]){
      if(!hasValue(detected.x_m)||!hasValue(detected.y_m))continue;
      node("line",{x1:origin.x_m,y1:origin.y_m,x2:detected.x_m,y2:detected.y_m,class:"sensor-ray"},layer);
      const point=node("circle",{cx:detected.x_m,cy:detected.y_m,r:3.2*pixel,class:"sensor-detection-point"},layer);node("title",{},point,`Észlelt ${detected.id} · ${fmt(detected.distance_m)} m · ${fmt(detected.speed_m_s)} m/s${fresh?"":" · elavult minta"}`);
    }
    const pedestrianNames={waiting:"várakozó gyalogos",crossing:"átkelő gyalogos",no_pedestrian_observed:"gyalogos nincs észlelve"};
    for(const detected of environment.crossings||[]){
      if(!hasValue(detected.x_m)||!hasValue(detected.y_m))continue;
      const point=node("circle",{cx:detected.x_m,cy:detected.y_m,r:4*pixel,class:`sensor-crossing-point ${detected.pedestrian_state==="no_pedestrian_observed"?"clear":""}`},layer);node("title",{},point,`${detected.id} · ismert térképi gyalogátkelő · ${pedestrianNames[detected.pedestrian_state]||"ismeretlen állapot"}${fresh?"":" · elavult minta"}. A gyalogos megfigyelése a térképi zebrához társítva; ez nem a zebra középpontjára mért távolsági találat.`);
    }
    const proximity=sample.proximity,channels=window.YardPerception.proximityChannels(sample);
    for(const channel of channels){
      const mount=channel.origin_world_m,ends=channel.beam_endpoints_world_m||[],hit=channel.hit_world_m;
      if(!hasValue(mount?.x_m)||!hasValue(mount?.y_m))continue;
      const visual=window.YardPerception.channelVisual(channel,fresh,proximity.thresholds_m),group=node("g",{class:"sensor-proximity-channel","data-channel":channel.id},layer);
      node("title",{},group,`${channel.label||channel.id} · ${visual.observed?`${fmt(visual.distance,2)} m a szerelési ponttól · ${channel.target?.label||channel.target?.id||"testfelület"}`:"nincs visszatérés a tesztelt sugarakon"}${fresh?"":" · elavult minta"}. Mentett világkoordináták; a nyalábhatár nem folytonos lefedettség.`);
      if(ends.length===2&&ends.every(p=>hasValue(p?.x_m)&&hasValue(p?.y_m)))node("path",{d:`M${mount.x_m} ${mount.y_m}L${ends[0].x_m} ${ends[0].y_m}L${ends[1].x_m} ${ends[1].y_m}Z`,class:"sensor-proximity-fan",stroke:visual.color,fill:visual.observed?visual.color:"none","fill-opacity":fresh ? .09 : .035},group);
      node("circle",{cx:mount.x_m,cy:mount.y_m,r:1.7*pixel,class:"sensor-proximity-mount",fill:visual.color},group);
      if(visual.observed&&hasValue(hit?.x_m)&&hasValue(hit?.y_m)){
        node("line",{x1:mount.x_m,y1:mount.y_m,x2:hit.x_m,y2:hit.y_m,class:"sensor-proximity-ray",stroke:visual.color},group);
        node("circle",{cx:hit.x_m,cy:hit.y_m,r:3*pixel,class:"sensor-proximity-hit",fill:visual.color},group);
      }
      if(channel.id===data.selectedChannel){group.classList.add("sensor-channel-selected");node("circle",{cx:mount.x_m,cy:mount.y_m,r:4.5*pixel,class:"sensor-selection-ring"},group);if(visual.observed&&hasValue(hit?.x_m)&&hasValue(hit?.y_m))node("circle",{cx:hit.x_m,cy:hit.y_m,r:6*pixel,class:"sensor-selection-ring"},group);}
    }
    node("text",{x:number(origin.x_m)+number(sample.range_m)+1.5,y:origin.y_m,class:"sensor-map-label"},layer,`${fmt(sample.range_m,1)} m környezet${channels.length?` · ${fmt(proximity.range_m,1)} m közelség`:""} · ${fresh?"virtuális érzékelés":"ELAVULT MINTA"}`);
  }
  function renderSensorOverlay(){
    const layer=$("sensors-layer");empty(layer);layer.classList.remove("sensor-layer-stale");for(const key of ["carId","sampleTime","fresh"])delete layer.dataset[key];
    const data=selectedSensors();if(!data||!$("show-sensors").checked)return;
    drawMapSensorOverlay(layer,{...data,selectedChannel:selectedProximityChannel("live")?.id},view.w/Math.max(260,$("yard-map").clientWidth||900));
  }
  function sampleCadenceText(sample){
    const period=reading(sample?.period_s,"s",3),nominal=hasValue(sample?.nominal_period_s)?`névleges ${reading(sample.nominal_period_s,"s",3)} · lépéshez igazított ${period}`:`mentett periódus ${period}`;
    return `${nominal}${Object.prototype.hasOwnProperty.call(sample||{},"sample_interval_s")?hasValue(sample.sample_interval_s)?` · előző eredeti mintáig ${reading(sample.sample_interval_s,"s",3)}`:" · nincs előző eredeti mintaidőköz":""}`;
  }
  function renderSensors(){
    const data=selectedSensors(),panel=$("sensor-panel"),status=$("sensor-panel-status"),active=(current?.cars||[]).find(c=>c.id===selectedCar);
    $("sensor-content").hidden=!data;$("sensor-empty").hidden=Boolean(data);panel.classList.toggle("is-stale",Boolean(data&&!data.fresh));
    if(!data){
      status.textContent=previewMap?"Elrendezés előnézete":selectedCar&&!active?"Archivált autó":"Nincs fogadott minta";status.className="data-badge neutral";
      $("sensor-panel-summary").textContent=previewMap?"Az előnézet tervezett pozíciókat mutat; az élő érzékelőréteg szünetel.":selectedCar&&!active?`${selectedCar} már nincs az aktív modellben. A mentett út külön visszakereshető.`:active?`${active.id} · az első virtuális érzékelőmintára várunk.`:"Válassz egy aktív autót a térképen vagy a flottalistában.";
      renderPerception("live",null);renderSensorOverlay();return;
    }
    const {car,sample,fresh}=data,pose=sample.pose,guidance=sample.guidance||{},odometry=sample.odometry||{},environment=sample.environment||{},vehicles=environment.vehicles||[],crossings=environment.crossings||[];
    status.textContent=fresh?(current?.paused?"Friss · futás szünetel":"Friss minta"):sample.fault_active?"Érzékelőhiba · régi minta":!serverReachable||!current?.connected?"Kapcsolat hiányzik":"Elavult minta";status.className=`data-badge ${fresh?"good":"warn"}`;
    $("sensor-panel-summary").textContent=`${car.id} · mintavétel: ${reading(sample.sample_sim_time_s)} szimulációs idő · minta kora: ${reading(sample.age_s)} · ${fresh?"MQTT-n fogadott virtuális érzékelőcsomag":"utolsó ismert megfigyelés, friss észlelésként nem használható"}`;
    $("sensor-pose").textContent=`${fmt(pose.x_m,2)}; ${fmt(pose.y_m,2)} m`;
    $("sensor-pose-bound").textContent=hasValue(sample.position_uncertainty_m)?`±${fmt(sample.position_uncertainty_m,2)} m (X, Y)`:"—";
    $("sensor-route-error").textContent=guidance.available&&hasValue(guidance.lateral_error_m)?`${fmt(guidance.lateral_error_m,3)} m`:"Nincs hozzárendelt út";
    $("sensor-route-heading").textContent=guidance.available&&hasValue(guidance.route_heading_deg)?`${fmt(guidance.route_heading_deg,1)}° · eltérés ${fmt(guidance.heading_error_deg,1)}°`:"—";
    $("sensor-range").textContent=`${reading(sample.range_m,"m")} · ${sampleCadenceText(sample)}`;
    $("sensor-detection-count").textContent=`${vehicles.length} autó · ${crossings.length} zebraállapot`;
    $("sensor-motion").textContent=`${reading(odometry.speed_m_s,"m/s")} · ${hasValue(pose.heading_deg)?`${fmt(pose.heading_deg,1)}°`:"—"}`;
    $("sensor-distance").textContent=reading(odometry.distance_m,"m");
    const pedestrianNames={waiting:"gyalogos vár",crossing:"gyalogos átkel",no_pedestrian_observed:"gyalogos nincs észlelve"};
    const detections=[...vehicles.map(v=>`${v.id}: ${reading(v.distance_m,"m")}`),...crossings.map(c=>`${c.id}: ${reading(c.distance_m,"m")} · ${pedestrianNames[c.pedestrian_state]||"ismeretlen"}`)];
    $("sensor-detections").textContent=detections.length?(fresh?"":"Utolsó mintában: ")+detections.join("; "):fresh?"Az érzékelési körben nincs másik autó vagy zebra.":"Az utolsó mintában nem volt másik autó vagy zebra.";
    $("sensor-sample-meta").textContent=`Frissességi határ: ${reading(sample.timeout_s)} szimulációs idő. Helyzaj: korlátos egyenletes, külön-külön X és Y; irányhibakorlát: ${hasValue(pose.heading_uncertainty_deg)?`±${fmt(pose.heading_uncertainty_deg,1)}°`:"—"}. Oldaleltérés: pozitív jobbra a haladás irányában. Sebesség és út: ideális modellből vett odometriajel. A kör középpontja a mintavételkori modellpozíció; a hibakorlát téglalapja méretarányos, a kereszt és az észlelési pontok mérete csak jelölés.${!$("show-sensors").checked?" A térképi érzékelőréteg ki van kapcsolva.":""}`;
    renderPerception("live",{sensors:sample,control:car.control||null,car_id:car.id,run_id:current.run_id,telemetry_sim_time:car.sim_time??current.sim_time,telemetry_source:"snapshot",utc:current.received_utc||null,fresh,archive:false,gap:false});renderSensorOverlay();
  }
  function renderFleet() {const root=$("fleet-list");empty(root);const cars=current?.cars||[];$("fleet-count").textContent=cars.length;if(!cars.length){const p=document.createElement("p");p.className="empty-state";p.textContent="Az első autó a gyártósori kijáratnál jelenik meg.";root.append(p);return;}for(const car of cars){const row=document.createElement("div");row.className=`fleet-row ${selectedCar===car.id?"selected":""}`;row.tabIndex=0;row.setAttribute("role","button");const dot=document.createElement("i");dot.className="fleet-car-dot";dot.style.background=colorFor(car.id);const main=document.createElement("div");main.className="fleet-main";const strong=document.createElement("strong");strong.textContent=car.id;const small=document.createElement("small");small.textContent=`${car.slot_id||"Nincs kijelölt hely"} · ${fmt(car.speed)} m/s`;main.append(strong,small);const state=document.createElement("span");state.className=`fleet-state ${["sensor_stop","communication_stop","frozen"].includes(car.state)?"stop":["pedestrian_stop","waiting_zone"].includes(car.state)?"alert":""}`;state.textContent=displayState(car);row.append(dot,main,state);const select=()=>selectCar(car.id);row.addEventListener("click",select);row.addEventListener("keydown",e=>{if(e.key==="Enter"||e.key===" "){e.preventDefault();select();}});root.append(row);}}

  const knownCars = new Set();
  const expandedLogIds = new Set();
  const categories = {decision:"Döntés",control:"Kezelői parancs",state:"Állapot",fault:"Hiba",lifecycle:"Életciklus",transport:"Adatátvitel"};
  let observedRun = null, carData = null, carBusy = false, carVersion = 0;
  let logCursor = null, logCursorStack = [], logNextBefore = null;
  let logsBusy = false, logsDirty = true, logVersion = 0, logLastSignature = "";
  const hasValue = value => value !== null && value !== undefined && Number.isFinite(Number(value));
  const reading = (value,unit="s",digits=1) => hasValue(value)?`${fmt(value,digits)} ${unit}`:"—";
  const html = (tag, attrs={}, text=null) => {const e=document.createElement(tag);for(const[k,v]of Object.entries(attrs)){if(k==="class")e.className=v;else e.setAttribute(k,v);}if(text!==null)e.textContent=String(text);return e;};
  async function getJSON(url,timeoutMs=5000) {
    const abort=new AbortController(),timeoutError=new Error(`A lekérés időtúllépés miatt megszakadt (${timeoutMs/1000} s). Próbáld újra.`);timeoutError.name="TimeoutError";
    const timer=setTimeout(()=>abort.abort(timeoutError),timeoutMs);
    try {const response=await fetch(url,{cache:"no-store",signal:abort.signal});if(!response.ok){let message=`HTTP ${response.status}`;try{message=(await response.json()).error||message;}catch(error){}throw new Error(message);}return await response.json();}
    catch(error){if(abort.signal.aborted&&(error===abort.signal.reason||error?.name==="AbortError"))throw timeoutError;throw error;}
    finally {clearTimeout(timer);}
  }
  function registerCars(cars) {
    let changed=false;for(const car of cars){const id=typeof car==="string"?car:car.id;if(id&&!knownCars.has(id)){knownCars.add(id);changed=true;}}
    if(!changed)return;
    const ids=[...knownCars].sort((a,b)=>a.localeCompare(b,undefined,{numeric:true}));
    const filter=$("log-car"),prior=filter.value;filter.replaceChildren(new Option("Összes jármű",""));for(const id of ids)filter.add(new Option(id,id));filter.value=ids.includes(prior)?prior:"";
    const inspector=$("inspect-car-select");inspector.replaceChildren(new Option("Válassz autót",""));for(const id of ids)inspector.add(new Option(id,id));inspector.value=selectedCar||"";
  }
  function resetRunCaches(runId) {
    observedRun=runId;selectedCar=null;carData=null;carVersion++;knownCars.clear();expandedLogIds.clear();pendingControls.clear();
    logCursor=null;logCursorStack=[];logNextBefore=null;logsDirty=true;logVersion++;logLastSignature="";
    for(const id of ["log-level","log-category","log-search"])$(id).value="";
    $("log-car").replaceChildren(new Option("Összes jármű",""));$("inspect-car-select").replaceChildren(new Option("Válassz autót",""));
    $("event-list").replaceChildren(html("tr",{},null));$("event-list").firstChild.append(html("td",{colspan:"5",class:"log-empty"},"Új futás: a napló és a járműtörténet betöltésére várunk."));
    empty($("car-history"));renderChart([]);$("log-records").textContent="0 rekord";$("log-error").hidden=true;renderLogNavigation();renderCarDetails();
  }
  function selectCar(id) {
    if(!id)return;const changed=selectedCar!==id;selectedCar=id;registerCars([id]);$("inspect-car-select").value=id;
    if(changed){carData=null;carVersion++;empty($("car-history"));renderChart([]);}
    if([...$("car-select").options].some(o=>o.value===id))$("car-select").value=id;
    if(current){renderCars();renderFleet();}renderCarDetails();renderSensors();
  }
  function renderWarnings() {const warnings=current?.warnings||[];$("warnings").hidden=!warnings.length;$("warnings").textContent=warnings.map(w=>typeof w==="string"?w:w.message||JSON.stringify(w)).join(" · ");}
  function renderObservability(state) {
    const mqtt=state.mqtt||{},logging=state.logging||{};
    $("mqtt-p95").textContent=reading(mqtt.latency_p95_ms,"ms");$("snapshot-age").textContent=hasValue(mqtt.last_message_age_s)?`Állapotkép kora: ${fmt(mqtt.last_message_age_s,2)} s`:"Állapotkép kora: —";
    $("map-selection").textContent=previewMap?"Tervezett elrendezés · koordináták méterben":selectedCar?`Kijelölt: ${selectedCar}${(state.cars||[]).some(c=>c.id===selectedCar)?"":" · archivált"}`:"Válassz járművet a részletekhez";
    $("log-records").textContent=hasValue(logging.records)?`${number(logging.records).toLocaleString("hu-HU")} rekord`:"— rekord";
    const truncated=number(logging.telemetry_truncated)>0,ring=logging.raw_policy==="recent_per_run_ring";
    $("logging-limit-note").textContent=ring?`Nyers MQTT: gördülő fájlok · ${number(logging.raw_removed_segments)} régebbi rész törölve. A szemantikus napló és a fogadott járműutak a lemezen megmaradnak.`:truncated?`${number(logging.telemetry_records).toLocaleString("hu-HU")} megőrzött minta · ${number(logging.telemetry_truncated).toLocaleString("hu-HU")} régi minta a kereten kívül`:logging.detailed_raw_limited?"Régi futás: a nyers MQTT-részlet mentése elérte a korlátját. Az események és a tárolt telemetria külön archívumban kereshetők.":hasValue(logging.telemetry_records)?`${number(logging.telemetry_records).toLocaleString("hu-HU")} telemetriaminta · MQTT-forrás`:"MQTT-n fogadott adatokból";
    $("logging-limit-note").classList.toggle("limited",!ring&&(Boolean(logging.detailed_raw_limited)||truncated));
    renderObserverStatus(state);
    renderTransportFreshness(state);
    renderTransportCapacity(state);
  }
  function renderObserverStatus(state){
    const note=$("mqtt-observer-note"),observer=state?.mqtt?.observer;
    if(!observer||typeof observer!=="object"||Array.isArray(observer)){note.textContent="A helyi MQTT-fogadó feldolgozási diagnosztikája még nem érhető el.";note.title="";note.classList.toggle("is-backlogged",false);return;}
    const count=value=>Number.isSafeInteger(value)&&value>=0?value.toLocaleString("hu-HU"):null;
    const milliseconds=value=>typeof value==="number"&&Number.isFinite(value)&&value>=0?`${fmt(value,2)} ms`:"—";
    const pending=count(observer.backlog_messages),messages=count(observer.last_batch_messages),budget=count(observer.batch_message_budget);
    note.textContent=`Helyi MQTT-feldolgozás: ${pending===null?"a várakozó üzenetszám ismeretlen":`kb. ${pending} üzenet vár`}. Utolsó adag: ${messages??"—"} üzenet / ${milliseconds(observer.last_batch_wall_ms)}. Adagonkénti célkeret: ${budget??"—"} üzenet, ${milliseconds(observer.batch_wall_budget_ms)}. Az ellenőrzés teljes üzenet után történik; egy feldolgozás vagy állapotellenőrzés túlfuthat ezen. Ez helyi feldolgozási idő, nem hálózati késleltetés.`;
    note.title=`${count(observer.batches)??"—"} feldolgozási adag · ${count(observer.dequeued_messages)??"—"} kivett üzenet · legnagyobb adagidő ${milliseconds(observer.max_batch_wall_ms)} · becsült sorcsúcs ${count(observer.peak_backlog_messages)??"—"} üzenet. A sorhossz a befejezett adag után megfigyelt, hozzávetőleges érték.`;
    note.classList.toggle("is-backlogged",Number.isSafeInteger(observer.backlog_messages)&&observer.backlog_messages>0);
  }
  function renderTransportFreshness(state){
    const note=$("transport-freshness-note"),mqtt=state?.mqtt||{},transport=mqtt.transport||{},observer=mqtt.observer||{};
    const valid=value=>typeof value==="number"&&Number.isFinite(value)&&value>=0;
    const age=value=>valid(value)?`${fmt(value,3)} s`:"—";
    const wait=value=>valid(value)?`${fmt(value,2)} ms`:"—";
    const count=value=>Number.isSafeInteger(value)&&value>=0?value.toLocaleString("hu-HU"):"—";
    note.textContent=`MQTT-érkezés kora: ${age(transport.last_message_age_s)} (összes feliratkozott téma); legutóbbi állapotkép-érkezés: ${age(transport.last_snapshot_age_s)}. Fogadó sorvárakozása: utolsó ${wait(observer.last_queue_wait_ms)}, P95 ${wait(observer.queue_wait_p95_ms)} / ${count(observer.queue_wait_sample_count)} megfigyelés. Kijelzett, már feldolgozott állapotkép kora a beérkezéstől: ${age(mqtt.last_message_age_s)}. Ezek helyi monoton faliórán mért értékek; a sorvárakozás a beérkezéstől a teljes üzenet feldolgozásának kezdetéig tart, a feldolgozás idejét nem tartalmazza. Nem szimulációs idő és nem hálózati késleltetés.`;
    note.title=`Érvényes MQTT-visszahívások: ${count(transport.callback_received_messages)} · broker: ${transport.broker_connected===true?"csatlakozik":transport.broker_connected===false?"nem csatlakozik":"ismeretlen"}. P95: a legutóbbi legfeljebb ${count(observer.queue_wait_window_limit)} érvényes sorvárakozás-mintából.`;
  }
  function renderTransportCapacity(state){
    const panel=$("transport-capacity-panel"),badge=$("transport-capacity-status"),received=state?.mqtt?.capacity,run=typeof state?.run_id==="string"?state.run_id:"";
    if(!panel||!badge||["transport-arrival-rate","transport-dequeue-rate","transport-queue-count","transport-queue-change","transport-capacity-window","transport-capacity-note","transport-rate-chart","transport-queue-chart"].some(id=>!$(id)))return;
    const valid=value=>typeof value==="number"&&Number.isFinite(value)&&value>=0;
    const signed=value=>typeof value==="number"&&Number.isFinite(value);
    const usable=Boolean(run&&received&&typeof received==="object"&&!Array.isArray(received)&&received.clock==="host_perf_counter"&&received.scope==="current_run_bounded_in_memory_observations"&&["collecting","observed","unavailable","stale"].includes(received.status)&&Array.isArray(received.history)&&received.history.length<=120);
    const data=usable?received:null,history=data?.history||[],measured=Boolean(data&&["observed","stale"].includes(data.status)&&valid(data.window_s)&&data.window_s>=1),rate=value=>measured&&valid(value)?`${fmt(value,1)}`:"—",queue=Number.isSafeInteger(data?.queue_messages)&&data.queue_messages>=0?data.queue_messages:null,growth=measured&&signed(data.queue_growth_hz)?data.queue_growth_hz:null,stale=data?.status==="stale";
    panel.dataset.runId=run;panel.dataset.httpUnavailable="false";panel.classList.toggle("is-stale",stale);
    $("transport-arrival-rate").textContent=rate(data?.arrival_rate_hz);$("transport-dequeue-rate").textContent=rate(data?.dequeue_rate_hz);$("transport-queue-count").textContent=queue===null?"—":queue.toLocaleString("hu-HU");
    $("transport-queue-change").textContent=growth===null?"A sor változásához még nincs értelmezhető időablak.":`${stale?"Korábbi megfigyelés: ":"A mért ablakban "}${growth>0?"nőtt":growth<0?"csökkent":"változatlan"} · ${growth>0?"+":""}${fmt(growth,1)} üzenet/s.`;
    const growing=!stale&&queue!==null&&queue>0&&growth!==null&&growth>0;
    badge.textContent=!data?"Mérés nem érhető el":stale?"Utolsó mérés elavult":data.status==="collecting"?"Mérési ablak gyűlik":data.status==="unavailable"?"Ráta még nem meghatározható":growing?"A sor nő a mért ablakban":queue===0?"A sor üres a mintában":queue!==null&&growth!==null&&growth<0?"A sor ürül a mért ablakban":"Fogadó forgalma megfigyelve";
    badge.className=`data-badge ${stale||growing?"warn":data?.status==="observed"?"good":"neutral"}`;
    const resetNote={counter_reset:"A mérési sorozat számláló-visszaállás miatt újraindult.",clock_regression:"A mérési sorozat óra-visszalépés miatt újraindult.",invalid_clock:"Érvénytelen mérési óra miatt új sorozatra várunk."}[data?.reset_reason]||"";
    $("transport-capacity-window").textContent=data?`Megfigyelt rátaablak: ${measured?`${fmt(data.window_s,2)} s`:"még gyűlik / nem értelmezhető"} · minta kora: ${valid(data.sample_age_s)?`${fmt(data.sample_age_s,2)} s`:"ismeretlen"}. ${history.length} megőrzött szerveroldali időpont az aktuális futásban.${resetNote?` ${resetNote}`:""}`:"A régi vagy hiányos állapotüzenetből nem számolunk rátákat; a böngésző lekérdezési ideje nem mérési óra.";
    $("transport-capacity-note").textContent=`A ráták a műszerfal helyi MQTT-fogadójának tényleges beérkezési és sorból kivételi számlálóiból, helyi monoton faliórán mért pozitív időablakra vonatkoznak. Az összes feliratkozott üzenettípus beleszámít; ez mért forgalom, nem igazolt maximális kapacitás, érzékelőfrekvencia vagy hálózati késleltetés. A sorhossz hozzávetőleges pillanatnyi megfigyelés. A grafikonok az aktuális futás legfeljebb 120 memóriában megőrzött időpontját mutatják; a vízszintes idő az aktuális mérési sorozat kezdetétől számított, számláló- vagy óra-visszaálláskor újraindul. A hiányzó értékeknél, időrendhibánál és a névleges mintaköz másfélszeresénél nagyobb résnél a vonalak megszakadnak.${growing?" Ha a sor tovább nő, a meglévő 1× vagy szünetkérés csökkentheti a terhelést; a végrehajtást az MQTT-visszajelzés igazolja.":""}`;
    const options={runId:run,sampleInterval:data?.sample_interval_s,stale};
    drawCapacityChart($("transport-rate-chart"),history,[{field:"arrival_rate_hz",label:"Beérkezés",color:"#188e99"},{field:"dequeue_rate_hz",label:"Feldolgozás",color:"#8263b9",dash:"5 3"}],{...options,unit:"üzenet/s",rates:true,height:170});
    drawCapacityChart($("transport-queue-chart"),history,[{field:"queue_messages",label:"Várakozó sor",color:"#b98a42"}],{...options,unit:"üzenet",rates:false,height:110});
  }
  function markTransportCapacityOffline(){
    const panel=$("transport-capacity-panel"),badge=$("transport-capacity-status"),windowNote=$("transport-capacity-window"),trend=$("transport-queue-change"),note=$("transport-capacity-note");
    if(panel){panel.classList.toggle("is-stale",true);panel.dataset.httpUnavailable="true";}
    if(badge){badge.textContent="Utolsó mérés · nincs frissítés";badge.className="data-badge warn";}
    if(windowNote)windowNote.textContent="A szerverkapcsolat kiesett; a kártyák és grafikonok az utolsó fogadott mérést mutatják. A frissesség jelenleg nem ellenőrizhető.";
    if(trend)trend.textContent="Korábbi mérés · a sor jelenlegi változása nem ellenőrizhető.";
    if(note)note.textContent="A megőrzött ráták a korábbi szerveroldali, helyi monoton faliórán mért ablakhoz tartoznak. A kapcsolat helyreállásáig nincs új minta; a jelenlegi sorhossz és annak változása nem állapítható meg a megőrzött grafikonból.";
    for(const id of ["transport-rate-chart","transport-queue-chart"]){const svg=$(id);if(svg){svg.classList.toggle("is-stale",true);svg.dataset.httpUnavailable="true";}}
  }
  function capacityChartSegments(history,field,interval,rates){
    const valid=value=>typeof value==="number"&&Number.isFinite(value)&&value>=0,period=valid(interval)&&interval>0?interval:null;
    const segments=[];let active=[],previous=null;
    const finish=()=>{if(active.length)segments.push(active);active=[];};
    for(const point of history){
      if(!point||typeof point!=="object"||!valid(point.elapsed_s)){finish();previous=null;continue;}
      if(previous!==null&&(point.elapsed_s<=previous||period===null||point.elapsed_s-previous>period*1.5))finish();
      previous=point.elapsed_s;
      const value=point[field],usable=valid(value)&&(rates?valid(point.window_s)&&point.window_s>=1:Number.isSafeInteger(value));
      if(!usable){finish();continue;}
      active.push({elapsed_s:point.elapsed_s,value});
    }
    finish();return segments;
  }
  function drawCapacityChart(svg,history,series,options){
    empty(svg);svg.dataset.runId=options.runId;svg.dataset.unit=options.unit;svg.dataset.httpUnavailable="false";svg.classList.toggle("is-stale",Boolean(options.stale));
    const width=640,height=options.height,left=48,right=14,top=19,bottom=25,plotWidth=width-left-right,plotHeight=height-top-bottom;
    svg.setAttribute("viewBox",`0 0 ${width} ${height}`);
    node("title",{},svg,`${series.map(item=>item.label).join(" és ")} · ${options.unit} · ${options.archive?"rögzített mintasorszám":"helyi eltelt idő"} · ${options.runId||"ismeretlen futás"}${options.stale?" · elavult megfigyelés":""}`);
    const groups=series.map(item=>({...item,segments:options.archive?transportArchiveChartSegments(history,item.field,options.rates):capacityChartSegments(history,item.field,options.sampleInterval,options.rates)})),points=groups.flatMap(group=>group.segments.flat());
    if(!points.length){node("text",{x:width/2,y:height/2,"text-anchor":"middle",class:"capacity-chart-empty"},svg,options.rates?"Még nincs értelmezhető szerveroldali rátaablak":"Még nincs megfigyelt sorhossz");return;}
    const times=options.archive?history.filter(point=>Number.isSafeInteger(point?.id)&&point.id>0).map(point=>point.id):history.filter(point=>typeof point?.elapsed_s==="number"&&Number.isFinite(point.elapsed_s)&&point.elapsed_s>=0).map(point=>point.elapsed_s),minTime=Math.min(...times),maxTime=Math.max(...times),span=maxTime-minTime;
    const maximum=Math.max(...points.map(point=>point.value)),step=maximum>100?50:maximum>10?10:1,rounded=maximum>0?Math.ceil(maximum*1.1/step)*step:1,yMax=Number.isFinite(rounded)?rounded:maximum;
    const x=time=>span>0?left+(time-minTime)/span*plotWidth:left+plotWidth/2,y=value=>top+plotHeight-value/yMax*plotHeight;
    node("text",{x:left,y:11,class:"capacity-axis-label"},svg,options.unit);
    for(const value of [0,yMax/2,yMax]){const yy=y(value);node("line",{x1:left,y1:yy,x2:width-right,y2:yy,class:"capacity-grid-line"},svg);node("text",{x:left-7,y:yy+3,"text-anchor":"end",class:"capacity-axis-label"},svg,fmt(value,value%1?1:0));}
    const ticks=span>0?[minTime,minTime+span/2,maxTime]:[minTime];
    for(const time of ticks)node("text",{x:x(time),y:height-7,"text-anchor":"middle",class:"capacity-axis-label"},svg,options.archive?`#${fmt(Math.round(time),0)}`:`${fmt(time,1)} s`);
    for(const group of groups)for(const segment of group.segments){
      if(segment.length>1)node("polyline",{points:segment.map(point=>`${x(point.elapsed_s).toFixed(2)},${y(point.value).toFixed(2)}`).join(" "),fill:"none",stroke:group.color,"stroke-width":2.1,"stroke-linejoin":"round","stroke-linecap":"round","stroke-dasharray":group.dash||"",class:"capacity-series-line","data-series":group.field,"data-start-time":segment[0].elapsed_s,"data-end-time":segment.at(-1).elapsed_s},svg);
      const last=segment.at(-1);node("circle",{cx:x(last.elapsed_s),cy:y(last.value),r:segment.length===1?2.6:2,fill:group.color,class:"capacity-series-point","data-series":group.field,"data-time":last.elapsed_s,"data-value":last.value},svg);
    }
  }
  // Operational review and observed fault resolution are separate lifecycles.
  // Polling reads bounded pages; only an explicit operator action acknowledges.
  let operations=null,operationsVersion=0,operationsLoading=false,operationsRunPinned=false;
  let operationsFilters={status:"all",rule:"all",severity:"all",car_id:"",search:""};
  let operationsReport=null,operationsReportVersion=0,operationsReportLoading=false,operationsObservedRun=null,operationsRunsTransitionToken=0;
  const operationsDrafts=new Map(),operationsAcknowledging=new Set();
  function operationsIdentity(){return JSON.stringify([$("operations-run")?.value||"",operationsFilters]);}
  function operationsFilterDraft(includeText=true){
    const filters={status:$("operations-filter")?.value||"all",rule:$("operations-rule")?.value||"all",severity:$("operations-severity")?.value||"all",car_id:includeText?$("operations-car-filter")?.value.trim()||"":operationsFilters.car_id,search:includeText?$("operations-search")?.value.trim()||"":operationsFilters.search};
    if(!["all","active","resolved","unacknowledged","acknowledged"].includes(filters.status)||!["all","communication_unavailable","processing_lag","sensor_unavailable","worker_stopped"].includes(filters.rule)||!["all","warning","error"].includes(filters.severity))throw new Error("Érvénytelen választott szűrő.");
    if(filters.car_id&&!/^[A-Za-z0-9_-]{1,64}$/.test(filters.car_id))throw new Error("Az autóazonosító legfeljebb 64 betű, szám, kötőjel vagy aláhúzás lehet.");
    if([...filters.search].length>128)throw new Error("A keresőszöveg legfeljebb 128 karakter lehet.");return filters;
  }
  function renderOperationsFilters(){
    const host=$("operations-filter-note");if(!host)return;
    const labels={all:"Összes",active:"Aktív",resolved:"Megszűnt",unacknowledged:"Nyugtázásra vár",acknowledged:"Nyugtázott",communication_unavailable:"MQTT-kapcsolat",processing_lag:"Feldolgozás",sensor_unavailable:"Érzékelőjel",worker_stopped:"Háttérfolyamat",warning:"Figyelmeztetés",error:"Hiba"};
    const draftCar=$("operations-car-filter")?.value.trim()||"",draftSearch=$("operations-search")?.value.trim()||"",pending=draftCar!==operationsFilters.car_id||draftSearch!==operationsFilters.search;
    host.textContent=`Alkalmazott szűrők: ${labels[operationsFilters.status]} · típus: ${labels[operationsFilters.rule]} · súlyosság: ${labels[operationsFilters.severity]} · autó: ${operationsFilters.car_id||"mind"} · szöveg: ${operationsFilters.search?`„${operationsFilters.search}”`:"nincs"}.${pending?" A szerkesztett szöveg még nincs alkalmazva; Enter vagy Alkalmazás rögzíti.":" A szűrés a szerveren, a teljes incidenshatárig történik. A szöveget Enterrel vagy Alkalmazással rögzítsd."} A keresőszöveg legfeljebb 128 karakter. A riport és az export minden epizódot tartalmaz a saját határáig.`;
    host.classList.toggle("has-draft",pending);
  }
  function applyOperationsFilters(){
    let filters;try{filters=operationsFilterDraft();}catch(error){showToast(error.message,true);renderOperationsFilters();return;}
    operationsFilters=filters;$("operations-car-filter").value=filters.car_id;$("operations-search").value=filters.search;changeOperationsSelection();
  }
  function resetOperationsFilters(){
    operationsFilters={status:"all",rule:"all",severity:"all",car_id:"",search:""};for(const [id,value]of [["operations-filter","all"],["operations-rule","all"],["operations-severity","all"],["operations-car-filter",""],["operations-search",""]])if($(id))$(id).value=value;changeOperationsSelection();
  }
  function operationsSearchMismatch(item,search){
    if(!search)return false;
    const values=[item.title,item.advice,item.subject,item.car_id,item.rule,item.note].filter(value=>typeof value==="string");
    // Python's full Unicode casefold has no browser equivalent. Verify ASCII
    // rows literally; non-ASCII matching is owned by the server and echo guard.
    if(!/^[\x00-\x7f]*$/.test(search)||values.some(value=>!/^[\x00-\x7f]*$/.test(value)))return false;
    const folded=search.toLowerCase();return !values.some(value=>value.toLowerCase().includes(folded));
  }
  function validateOperationsReport(result,run,until){
    const count=value=>Number.isSafeInteger(value)&&value>=0,keys=["total","active","resolved","acknowledged","unacknowledged"],ruleKeys=["communication_unavailable","processing_lag","sensor_unavailable","worker_stopped","other"];
    if(!result||result.schema_version!==1||result.scope!=="episode_prefix_current_lifecycle_snapshot"||result.run_id!==run||typeof result.available!=="boolean"||!count(result.until_id)||result.until_id!==until||typeof result.captured_at_utc!=="string"||!result.captured_at_utc.endsWith("Z")||!Number.isFinite(Date.parse(result.captured_at_utc))||!Array.isArray(result.rules))throw new Error("Hiányos vagy eltérő futású / incidenshatárú riport.");
    if(!result.available){if(result.counts!==null||result.durations!==null||result.recurrent_episodes!==null||result.rules.length)throw new Error("Ellentmondó riportelérhetőség.");return result;}
    const validCounts=values=>values&&keys.every(key=>count(values[key]))&&values.active+values.resolved===values.total&&values.acknowledged+values.unacknowledged===values.total;
    if(!validCounts(result.counts)||result.counts.total>result.until_id||!count(result.recurrent_episodes)||result.recurrent_episodes>Math.max(0,result.counts.total-1)||result.rules.length!==ruleKeys.length||!result.durations)throw new Error("Érvénytelen riportösszesítők.");
    const sums=Object.fromEntries(keys.map(key=>[key,0]));
    for(let index=0;index<ruleKeys.length;index++){const row=result.rules[index];if(!row||row.rule!==ruleKeys[index]||typeof row.title!=="string"||!row.title.trim()||!validCounts(row))throw new Error("Érvénytelen szabályeloszlás.");for(const key of keys)sums[key]+=row[key];}
    if(keys.some(key=>sums[key]!==result.counts[key]))throw new Error("A szabályeloszlás nem egyezik az összesítővel.");
    for(const [key,eligible]of [["confirmation",result.counts.total],["resolved_active",result.counts.resolved],["acknowledgment",result.counts.acknowledged]]){const metric=result.durations[key];if(!metric||!count(metric.valid_count)||!count(metric.excluded_count)||metric.valid_count+metric.excluded_count!==eligible)throw new Error("Érvénytelen időminta-szám.");const values=[metric.min_s,metric.max_s,metric.mean_s];if(metric.valid_count===0?values.some(value=>value!==null):values.some(value=>typeof value!=="number"||!Number.isFinite(value)||value<0)||metric.min_s>metric.mean_s||metric.mean_s>metric.max_s)throw new Error("Érvénytelen időtartam-összesítő.");}
    return result;
  }
  function invalidateOperationsReport(){
    operationsReportVersion++;operationsReportLoading=false;operationsReport=null;renderOperationsReport();
  }
  function operationsReportAvailable(){
    const run=$("operations-run")?.value;
    return Boolean(serverReachable&&!operationsLoading&&!operationsReportLoading&&operations?.runId===run&&operations.data?.available&&!operations.stale&&operationsReport?.runId===run&&operationsReport.data?.available&&!operationsReport.stale);
  }
  function renderOperationsReport(){
    const panel=$("operations-report-panel");if(!panel)return;
    const run=$("operations-run")?.value,view=operationsReport?.runId===run?operationsReport:null,data=view?.data,known=data?.available===true;
    panel.dataset.runId=view?.runId||run||"";panel.dataset.untilId=data?String(data.until_id):"";panel.classList.toggle("is-stale",Boolean(view?.stale));
    const blocked=Boolean(!serverReachable||operations?.stale),status=$("operations-report-status");status.textContent=operationsReportLoading?"Riport rögzítése…":view?.stale?"Megőrzött riport · frissítés sikertelen":blocked?"Kapcsolat / megfigyelés elavult":!data?"Még nincs rögzített riport":!known?"Nincs incidensarchívum":"Rögzített riport";status.className=`data-badge ${view?.stale||blocked?"warn":"neutral"}`;
    $("operations-report-error").hidden=!view?.error;$("operations-report-error").textContent=view?.error||"";
    $("operations-report-refresh").disabled=operationsReportLoading||operationsLoading||!serverReachable||!run||operations?.runId!==run||!operations.data||operations.stale;
    $("operations-report-origin").textContent=data?`Futás: ${data.run_id} · rögzítés (UTC): ${data.captured_at_utc} · incidenshatár: #${data.until_id}. Minden állapot, az első epizódtól a határig; a listaszűrő és a lapozás nem szűkíti.`:"A riportot a kiválasztott futás betöltött incidenshatáráig rögzítheted. A riport csak a gomb megnyomására változik.";
    $("operations-report-note").textContent=data&&!known?"Ebben a korábbi futásban nincs incidensarchívum. A hiányzó mérés nem jelent nulla eltérést vagy hibamentes működést.":"Az UTC-idők a gazdagép diagnosztikai életciklusát mérik; nem járműállásidők. Új rögzítéskor ugyanazon epizódok megszűnése és nyugtázása is változhat.";
    const seconds=value=>typeof value==="number"&&Number.isFinite(value)?`${fmt(value,2)} s`:"—",samples=metric=>metric?`${metric.valid_count.toLocaleString("hu-HU")} érvényes · ${metric.excluded_count.toLocaleString("hu-HU")} kizárt időminta`:"Érvényes / kizárt időminták: —";
    $("operations-report-resolved-mean").textContent=known?seconds(data.durations.resolved_active.mean_s):"—";$("operations-report-ack-mean").textContent=known?seconds(data.durations.acknowledgment.mean_s):"—";$("operations-report-recurrent").textContent=known?data.recurrent_episodes.toLocaleString("hu-HU"):"—";
    $("operations-report-resolved-samples").textContent=samples(known?data.durations.resolved_active:null);$("operations-report-ack-samples").textContent=samples(known?data.durations.acknowledgment:null);
    $("operations-report-confirmation").textContent=known?`Első hibás megfigyelés → megerősítés: átlag ${seconds(data.durations.confirmation.mean_s)} · ${samples(data.durations.confirmation)}.`:"Első hibás megfigyelés → megerősítés: —";
    $("operations-report-duration-details").textContent=known?[["confirmation","Megerősítés"],["resolved_active","Megszűnt epizód aktív ideje"],["acknowledgment","Nyugtázási késés"]].map(([key,label])=>`${label}: minimum ${seconds(data.durations[key].min_s)}, maximum ${seconds(data.durations[key].max_s)}.`).join(" "):"Minimum / maximum időtartamok: —";
    $("operations-report-counts").textContent=known?`${data.counts.total.toLocaleString("hu-HU")} epizód · ${data.counts.active.toLocaleString("hu-HU")} aktív · ${data.counts.resolved.toLocaleString("hu-HU")} megszűnt · ${data.counts.acknowledged.toLocaleString("hu-HU")} nyugtázott · ${data.counts.unacknowledged.toLocaleString("hu-HU")} nyugtázásra vár`:"Az epizódok állapot szerinti darabszáma: —";
    const rows=$("operations-report-rules");empty(rows);if(known){const maximum=Math.max(1,...data.rules.map(row=>row.total));for(const row of data.rules){const tr=html("tr"),rule=html("th",{scope:"row"},row.title),distribution=html("td",{class:"operations-report-distribution"}),bar=html("span",{class:"operations-report-bar","aria-hidden":"true"}),fill=html("span");fill.style.width=`${row.total/maximum*100}%`;bar.append(fill);distribution.append(html("span",{class:"operations-report-rule-count"},row.total.toLocaleString("hu-HU")),bar);tr.append(rule,distribution,html("td",{},row.active.toLocaleString("hu-HU")),html("td",{},row.unacknowledged.toLocaleString("hu-HU")));rows.append(tr);}}else{const tr=html("tr");tr.append(html("td",{colspan:4},data?"Nincs incidensarchívum; az eloszlás ismeretlen.":"Rögzíts riportot a szabályeloszlás megjelenítéséhez."));rows.append(tr);}
    const allowed=operationsReportAvailable();for(const format of ["csv","json"]){const link=$("operations-export-"+format);link.setAttribute("aria-disabled",String(!allowed));if(allowed){const query=new URLSearchParams({run_id:data.run_id,until_id:String(data.until_id),format});link.href=`/api/operations/export?${query}`;link.title="Friss életciklus-pillanatkép a riporttal azonos futásból és incidenshatárig; az állapotok eltérhetnek a rögzített riporttól.";}else{link.removeAttribute("href");link.title="A letöltéshez sikeresen rögzített, elérhető riport és szerverkapcsolat szükséges.";}}
  }
  async function loadOperationsReport(){
    const run=$("operations-run")?.value,until=operations?.data?.until_id;
    if(!run||operationsReportLoading||operationsLoading||!serverReachable||operations?.runId!==run||!operations.data||operations.stale||!Number.isSafeInteger(until)||until<0)return;
    const prior=operationsReport?.runId===run?operationsReport:null,version=++operationsReportVersion;operationsReportLoading=true;operationsReport=prior||{runId:run,data:null,stale:false,error:""};operationsReport.error="";renderOperationsReport();
    const query=new URLSearchParams({run_id:run,until_id:String(until)});
    try{const data=await getJSON(`/api/operations/report?${query}`,10000);if(version!==operationsReportVersion||$("operations-run")?.value!==run)return;validateOperationsReport(data,run,until);operationsReport={runId:run,data,stale:false,error:""};}
    catch(error){if(version===operationsReportVersion&&$("operations-run")?.value===run){operationsReport.stale=true;operationsReport.error=`A riport nem rögzíthető (${error.message}).${operationsReport.data?" A korábbi rögzítés látható; a letöltés sikeres új rögzítésig nem érhető el.":" Nincs értelmezhető riport."}`;}}
    finally{if(version===operationsReportVersion){operationsReportLoading=false;renderOperationsReport();}}
  }
  function normalizeOperationsRunLabels(){
    const selector=$("operations-run"),live=current?.run_id;if(!selector||!live)return;
    const selected=selector.value;if(![...selector.options].some(option=>option.value===live))selector.add(new Option(`Aktuális futás · ${live}`,live));
    for(const option of selector.options){if(!option.value)continue;option.textContent=`${option.value===live?"Aktuális futás":"Korábbi futás"} · ${option.dataset.startedAt||option.value} · ${option.value.slice(0,8)}`;}if(selected)selector.value=selected;
  }
  function observeOperationsRun(){
    const run=current?.run_id;if(!run||operationsObservedRun===run)return;
    operationsObservedRun=run;normalizeOperationsRunLabels();const token=++operationsRunsTransitionToken;
    setTimeout(()=>{if(token===operationsRunsTransitionToken&&current?.run_id===run)loadOperationsRuns();},0);
  }
  function operationsEvidenceValid(evidence){
    return Boolean(evidence&&Number.isSafeInteger(evidence.transport_sample_id)&&evidence.transport_sample_id>0&&Number.isSafeInteger(evidence.telemetry_until_id)&&evidence.telemetry_until_id>=0&&Number.isSafeInteger(evidence.events_until_id)&&evidence.events_until_id>=0&&typeof evidence.utc==="string");
  }
  function validateOperationsResponse(result,run,status,before=null,until=null,filters={status,rule:"all",severity:"all",car_id:"",search:""}){
    const count=value=>Number.isSafeInteger(value)&&value>=0,summary=result?.summary;
    if(!result||result.schema_version!==1||result.run_id!==run||typeof result.current_live!=="boolean"||typeof result.available!=="boolean"||!count(result.until_id)||until!==null&&result.until_id!==until||!summary||!["attention","healthy","unknown"].includes(summary.health)||typeof summary.health_reason!=="string"||![summary.active,summary.unacknowledged,summary.total,summary.known_rules,summary.total_rules].every(count)||summary.active>summary.total||summary.unacknowledged>summary.total||summary.known_rules>summary.total_rules||!Array.isArray(result.items)||result.items.length>50||result.items.length>summary.total||!result.thresholds||typeof result.thresholds!=="object")throw new Error("Hiányos vagy eltérő futású diagnosztikai oldal.");
    const hasFilters=result.filters!==undefined||result.matching_count!==undefined,defaults=filters.status==="all"&&filters.rule==="all"&&filters.severity==="all"&&!filters.car_id&&!filters.search;
    if(hasFilters){
      if(!result.filters||["status","rule","severity","car_id","search"].some(key=>result.filters[key]!==filters[key]))throw new Error("Eltérő alkalmazott incidensszűrők.");
      if(result.available?(!count(result.matching_count)||result.matching_count>summary.total||result.matching_count>result.until_id||result.items.length>result.matching_count):result.matching_count!==null)throw new Error("Érvénytelen vagy ismeretlen szűrt találatszám.");
    }else if(!defaults)throw new Error("A szűrt diagnosztikai válasz nem igazolja az alkalmazott szűrőket.");
    let cursor=before??result.until_id+1;
    for(const item of result.items){if(!item||!Number.isSafeInteger(item.id)||item.id<=0||item.id>=cursor||item.id>result.until_id||typeof item.active!=="boolean"||typeof item.acknowledged!=="boolean"||!Number.isSafeInteger(item.revision)||item.revision<1||!["warning","error"].includes(item.severity)||typeof item.rule!=="string"||typeof item.title!=="string"||typeof item.opened_utc!=="string"||!operationsEvidenceValid(item.opening)||!operationsEvidenceValid(item.latest)||item.latest.transport_sample_id<item.opening.transport_sample_id||status==="active"&&!item.active||status==="resolved"&&item.active||status==="unacknowledged"&&item.acknowledged||status==="acknowledged"&&!item.acknowledged||filters.rule!=="all"&&item.rule!==filters.rule||filters.severity!=="all"&&item.severity!==filters.severity||filters.car_id&&item.car_id!==filters.car_id||operationsSearchMismatch(item,filters.search))throw new Error("Érvénytelen, más szűrőhöz tartozó incidens vagy lapozási sorrend.");cursor=item.id;}
    if(result.next_before!==null&&(!result.items.length||result.next_before!==cursor)||!result.available&&(result.items.length||summary.total||summary.health!=="unknown"))throw new Error("Ellentmondó diagnosztikai elérhetőség vagy lapozás.");return result;
  }
  function operationsAcknowledgmentAllowed(item,view=operations){
    return Boolean(view?.data?.current_live&&view.data.available&&!view.stale&&!operationsLoading&&serverReachable&&current?.run_id===view.runId&&!item.acknowledged&&!operationsAcknowledging.has(`${view.runId}:${item.id}`));
  }
  function renderOperationsSafety(){
    observeOperationsRun();renderOperationsReport();
    const list=$("operations-list");if(!list||!operations?.data)return;
    if($("operations-panel")?.dataset.currentRunId!==(current?.run_id||"")||$("operations-panel")?.dataset.serverReachable!==String(serverReachable)||$("operations-panel")?.dataset.stale!==String(Boolean(operations.stale))){renderOperations();return;}
    for(const button of list.querySelectorAll("[data-ack-id]")){const item=operations.data.items.find(item=>String(item.id)===button.dataset.ackId);button.disabled=!item||!operationsAcknowledgmentAllowed(item);}
  }
  function renderOperations(){
    const panel=$("operations-panel"),list=$("operations-list");if(!panel||!list)return;
    const view=operations,data=view?.data,summary=data?.summary,known=data?.available===true,effectiveLive=data?.current_live===true&&current?.run_id===view?.runId,liveChanged=data?.current_live===true&&!effectiveLive;
    panel.dataset.runId=view?.runId||$("operations-run")?.value||"";panel.dataset.currentRunId=current?.run_id||"";panel.dataset.serverReachable=String(serverReachable);panel.dataset.stale=String(Boolean(view?.stale));panel.classList.toggle("is-stale",Boolean(view?.stale)||!serverReachable);
    const badge=$("operations-status");badge.textContent=operationsLoading?"Diagnosztika betöltése…":view?.stale?"Megőrzött oldal · frissítés sikertelen":!serverReachable?"Szerverkapcsolat elavult":!data?"Megfigyelésre vár":liveChanged?"Korábbi futás · frissítés szükséges":!known?"Korábbi futás · nincs incidensarchívum":summary.health==="attention"?"Kezelői figyelmet igényel":summary.health==="healthy"?"Figyelt szabályok rendben":"Megfigyelés hiányos / történeti";badge.className=`data-badge ${!serverReachable||view?.stale?"warn":liveChanged?"neutral":summary?.health==="attention"?"warn":summary?.health==="healthy"?"good":"neutral"}`;
    for(const key of ["active","unacknowledged","total"])$("operations-"+key).textContent=known?summary[key].toLocaleString("hu-HU"):"—";
    $("operations-error").hidden=!view?.error;$("operations-error").textContent=view?.error||"";
    renderOperationsFilters();const matched=$("operations-matching-count");if(matched)matched.textContent=data?.available===false?"Szűrt találatszám: — · nincs incidensarchívum":!Number.isSafeInteger(data?.matching_count)?"Szűrt találatszám: — · még nincs igazolt keresési eredmény":`${view?.stale||!serverReachable?"Megőrzött találatszám":operationsLoading?"Korábbi találatszám · betöltés":"Szűrt találatok"}: ${data.matching_count.toLocaleString("hu-HU")} · a teljes #${data.until_id} incidenshatárig, nem csak ezen az oldalon.`;
    $("operations-note").textContent=data?`Futás: ${view.runId} · ${data.current_live&&current?.run_id===view.runId?"élő megfigyelés":"archivált / korábbi futás, csak olvasás"}. ${summary.health_reason} A nyugtázás a kezelői felülvizsgálatot rögzíti; az aktív hiba ettől nem szűnik meg.`:"Az automatikus diagnosztika fogadott adatokat vizsgál. A kezelői nyugtázás a felülvizsgálatot rögzíti; a hiba megszűnését külön megfigyelés igazolja.";
    $("operations-observation").textContent=data?`Diagnosztikai megfigyelés (UTC): ${summary.observation_utc||"ismeretlen"} · értelmezhető szabályok: ${summary.known_rules}/${summary.total_rules}. Az összesítők a teljes futásra vonatkoznak, a szűrő és a lapozás nem változtatja meg őket.`:"Utolsó diagnosztikai megfigyelés: ismeretlen.";
    const quality=$("operations-rule-quality");if(quality){empty(quality);for(const [rule,label]of [["communication_unavailable","MQTT-kapcsolat"],["processing_lag","Feldolgozás"],["sensor_unavailable","Érzékelőkeretek"],["worker_stopped","Futó komponensek"]]){const status=!serverReachable||liveChanged||view?.stale||effectiveLive&&summary?.observation_fresh===false?"unknown":summary?.quality?.[rule]||"unknown",text={healthy:"ép megfigyelés",fault:"hibás megfigyelés",unknown:"ismeretlen"}[status]||"ismeretlen";quality.append(html("span",{class:`operations-rule ${status==="fault"?"warn":status==="healthy"?"good":"neutral"}`},`${label} · ${data&&!effectiveLive?"mentett: ":""}${text}`));}}
    const thresholds=data?.thresholds,seconds=value=>typeof value==="number"&&Number.isFinite(value)?`${fmt(value,2)} s`:"ismeretlen";
    $("operations-thresholds").textContent=thresholds?`Kommunikációs kor: ${seconds(thresholds.communication_timeout_s)}; feldolgozott állapotkép kora: ${seconds(thresholds.processing_timeout_s)}; friss érzékelőjelentéshez állapotkép: ${seconds(thresholds.fresh_snapshot_s)}. Megerősítés: ${Number.isInteger(thresholds.fault_observations)?thresholds.fault_observations:"ismeretlen"} egymást követő hibás, megszűnés: ${Number.isInteger(thresholds.healthy_observations)?thresholds.healthy_observations:"ismeretlen"} ép megfigyelés. Indulási türelmi idő: ${seconds(thresholds.startup_grace_s)}; megfigyelési lépés: ${seconds(thresholds.observation_interval_s)}; hosszabb megfigyelési rés: ${seconds(thresholds.observation_gap_s)}.`:"A diagnosztikai küszöbök betöltésére várunk.";
    empty(list);
    for(const item of data?.items||[]){
      const key=`${view.runId}:${item.id}`,card=html("li",{class:`operations-incident ${item.severity}${item.active?" is-active":" is-resolved"}`,"data-incident-id":item.id}),heading=html("div",{class:"operations-incident-heading"}),title=html("div");title.append(html("small",{},`#${item.id} · ${item.car_id||"Rendszer"} · ${item.severity==="error"?"Hiba":"Figyelmeztetés"}`),html("h3",{},item.title));const badges=html("div",{class:"operations-incident-badges"});badges.append(html("span",{class:`data-badge ${item.active?"warn":"neutral"}`},item.active?"Aktív eltérés":"Megszűnt eltérés"),html("span",{class:`data-badge ${item.acknowledged?"good":"neutral"}`},item.acknowledged?"Nyugtázva":"Nyugtázásra vár"));heading.append(title,badges);card.append(heading,html("p",{class:"operations-advice"},item.advice||"A mentett nyitási bizonyíték alapján vizsgálható az eltérés."));
      const facts=html("dl",{class:"operations-incident-facts"});for(const [label,value]of [["Megerősítve (UTC)",item.opened_utc],["Utolsó megfigyelés (UTC)",item.last_seen_utc],["Megszűnt (UTC)",item.resolved_utc||"még nincs ép megfigyeléssel igazolva"],["Nyugtázva (UTC)",item.acknowledged_utc||"még nincs kezelői nyugtázás"]]){const pair=html("div");pair.append(html("dt",{},label),html("dd",{},value||"ismeretlen"));facts.append(pair);}card.append(facts);
      const evidence=item.opening;card.append(html("p",{class:"operations-evidence-note"},`Nyitási bizonyíték: megfigyelés #${evidence.transport_sample_id} · #${evidence.telemetry_until_id} telemetria / #${evidence.events_until_id} esemény · ${evidence.utc} UTC. Utolsó bizonyíték: #${item.latest.transport_sample_id}.`));
      const measurements=Object.entries(item.measurements||{}).filter(([,value])=>value===null||["string","number","boolean"].includes(typeof value));if(measurements.length){const details=html("details",{class:"operations-measurements"});details.append(html("summary",{},"Megfigyelés mért adatai"));const labels={callback_snapshot_age_s:"MQTT-állapotkép fogadási kora [s]",processed_snapshot_age_s:"Feldolgozott állapotkép kora [s]",communication_fault_active:"Injektált kommunikációs kiesés",state:"Jelentett járműállapot",sampled:"Érzékelőmintavétel megtörtént",fresh:"Friss érzékelőkeret",fault_active:"Érzékelőhiba jelentve",worker_pid:"Komponens folyamatazonosítója",exit_code:"Komponens kilépési kódja",last_message_age_s:"MQTT-fogadás kora [s]",snapshot_age_s:"Feldolgozott állapotkép kora [s]",last_snapshot_age_s:"Fogadott állapotkép kora [s]",queue_messages:"Feldolgozási sor [üzenet]",sensor_fault_active:"Érzékelőhiba jelentve",sensor_fresh:"Friss érzékelőkeret",sensor_age_s:"Érzékelőkeret kora [s]",broker_connected:"MQTT-közvetítő elérhető",sim_time:"Szimulációs idő [s]"};const rows=html("dl");for(const [name,value]of measurements){const row=html("div");row.append(html("dt",{},labels[name]||name),html("dd",{},value===null?"ismeretlen":typeof value==="boolean"?value?"igen":"nem":typeof value==="number"?Number.isFinite(value)?fmt(value,3):"ismeretlen":String(value)));rows.append(row);}details.append(rows);card.append(details);}
      const actions=html("div",{class:"operations-incident-actions"}),open=html("button",{type:"button",class:"button secondary"},"Első hibás megfigyelés és járművek →");open.disabled=operationsLoading;open.addEventListener("click",()=>openOperationsEvidence(item,view));actions.append(open);
      if(item.acknowledged){card.append(html("p",{class:"operations-acknowledged-note"},item.note?`Kezelői megjegyzés: ${item.note}`:"Nyugtázva, megjegyzés nélkül."));}
      else{const label=html("label",{class:"operations-review-label"},"Kezelői megjegyzés (opcionális)"),input=html("textarea",{class:"operations-review-note",rows:2,maxlength:500,"aria-label":`Incidens #${item.id} kezelői megjegyzése`,placeholder:"A vizsgálat rövid eredménye…"});input.value=operationsDrafts.get(key)||"";input.disabled=!data.current_live||current?.run_id!==view.runId;input.addEventListener("input",()=>operationsDrafts.set(key,input.value));label.append(input);card.append(label);const acknowledge=html("button",{type:"button",class:"button secondary","data-ack-id":item.id},operationsAcknowledging.has(key)?"Nyugtázás rögzítése…":"Felülvizsgálva · nyugtázás");acknowledge.disabled=!operationsAcknowledgmentAllowed(item,view);acknowledge.addEventListener("click",()=>acknowledgeOperations(item,view));actions.append(acknowledge);}
      card.append(actions);list.append(card);
    }
    if(!data?.items?.length)list.append(html("li",{class:"operations-empty"},!data?"Még nincs betöltött diagnosztikai előzmény.":!known?"Ebben a korábbi futásban nem készült incidensarchívum; ez nem hibamentes működést jelent.":data.matching_count===0?"Az alkalmazott szűrőkhöz nincs találat a teljes incidenshatárig. A teljes futás összesítői és riportja ettől nem változnak.":"Ezen az oldalon nincs rögzített incidens. A szűrt találatszám a teljes incidenshatárra vonatkozik; a szabályok lefedettségét az összesítő jelzi."));
    $("operations-page").textContent=data?`${data.items.length} incidens ezen az oldalon · legújabb elöl · listahatár #${data.until_id}. Az életciklus frissülhet; a határ csak az új incidensek beemelését korlátozza.`:"Legfeljebb 50 incidens / oldal";$("operations-newer").disabled=operationsLoading||!view?.stack?.length;$("operations-older").disabled=operationsLoading||data?.next_before==null;$("operations-refresh").disabled=operationsLoading||!$("operations-run")?.value;
    renderOperationsReport();
  }
  async function loadOperations(mode="first"){
    const run=$("operations-run")?.value,filters={...operationsFilters},status=filters.status,identity=operationsIdentity();if(!run||operationsLoading)return;
    const prior=operations?.identity===identity?operations:null;if(mode==="older"&&prior?.data?.next_before==null||mode==="newer"&&!prior?.stack.length)return;
    const stack=mode==="first"?[]:[...(prior?.stack||[])];let before=mode==="first"?null:prior?.before??null,until=mode==="first"?null:prior?.data?.until_id??null;
    if(mode==="older"){stack.push(before);before=prior.data.next_before;}if(mode==="newer")before=stack.pop();if(mode==="refresh"&&before===null)until=null;
    const version=++operationsVersion;operationsLoading=true;if(!prior)operations={identity,runId:run,status,filters,data:null,before:null,stack:[],stale:false,error:""};else{prior.error="";}renderOperations();
    const query=new URLSearchParams({run_id:run,...filters,limit:"50"});if(before!==null)query.set("before",String(before));if(until!==null)query.set("until_id",String(until));
    try{const data=await getJSON(`/api/operations?${query}`,10000);if(version!==operationsVersion||identity!==operationsIdentity())return;validateOperationsResponse(data,run,status,before,until,filters);operations={identity,runId:run,status,filters,data,before,stack,stale:false,error:""};}
    catch(error){if(version===operationsVersion&&identity===operationsIdentity()){operations.stale=true;operations.error=`A diagnosztikai lekérés sikertelen (${error.message}).${operations.data?" A megőrzött oldal látható; nyugtázni sikeres frissítés után lehet.":" Nincs értelmezhető új megfigyelés."}`;}}
    finally{if(version===operationsVersion){operationsLoading=false;renderOperations();}}
  }
  function changeOperationsSelection(){
    try{operationsFilters=operationsFilterDraft(false);}catch(error){showToast(error.message,true);return;}
    if(operationsReport?.runId!==$("operations-run")?.value)invalidateOperationsReport();
    operationsRunPinned=true;operationsVersion++;operationsLoading=false;operations=null;renderOperations();loadOperations();
  }
  async function loadOperationsRuns(){
    const selector=$("operations-run");if(!selector)return;const generation=loadOperationsRuns.generation=(loadOperationsRuns.generation||0)+1;
    try{const result=await getJSON("/api/runs?limit=200"),runs=[...(result.runs||[])];let cursor=result.next_before;while(cursor){const page=await getJSON(`/api/runs?limit=200&before=${encodeURIComponent(cursor)}`);if(generation!==loadOperationsRuns.generation)return;runs.push(...(page.runs||[]));if(page.next_before===cursor)break;cursor=page.next_before;}
      if(generation!==loadOperationsRuns.generation)return;const prior=operations?.runId||(operationsRunPinned?selector.value:current?.run_id||result.current_run_id);selector.replaceChildren();for(const run of runs){if(typeof run.run_id!=="string")continue;const option=new Option(`${run.run_id===(current?.run_id||result.current_run_id)?"Aktuális futás":"Korábbi futás"} · ${run.started_at_utc||run.run_id} · ${run.run_id.slice(0,8)}`,run.run_id);option.dataset.startedAt=run.started_at_utc||"";selector.add(option);}if(prior&&![...selector.options].some(option=>option.value===prior))selector.add(new Option(`Korábbi futás · ${prior}`,prior));if(prior)selector.value=prior;if(!selector.options.length)selector.add(new Option("Még nincs mentett futás",""));normalizeOperationsRunLabels();renderOperations();if(!operations&&!operationsLoading&&selector.value)loadOperations();
    }catch(error){if(generation===loadOperationsRuns.generation){const host=$("operations-error");host.hidden=false;host.textContent=`A futáslista nem tölthető (${error.message}); a kiválasztott incidensek megmaradnak.`;}}
  }
  async function operationsPost(body){
    const abort=new AbortController(),timer=setTimeout(()=>abort.abort(),10000);
    try{const response=await fetch("/api/operations/acknowledge",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify(body),signal:abort.signal});let data;try{data=await response.json();}catch(error){throw new Error(`HTTP ${response.status} · a válasz nem értelmezhető`);}if(!response.ok)throw new Error(response.status===409?"Az incidens életciklusa megváltozott; frissítsd az oldalt a nyugtázás előtt.":data.error||`HTTP ${response.status}`);return data;}
    finally{clearTimeout(timer);}
  }
  async function acknowledgeOperations(item,view=operations){
    if(view!==operations||view.identity!==operationsIdentity()||!view.data.items.includes(item)||!operationsAcknowledgmentAllowed(item,view))return;
    const key=`${view.runId}:${item.id}`,note=operationsDrafts.get(key)||"";if(note.length>500){showToast("A kezelői megjegyzés legfeljebb 500 karakter lehet.",true);return;}
    operationsAcknowledging.add(key);renderOperationsSafety();
    try{const result=await operationsPost({run_id:view.runId,id:item.id,expected_revision:item.revision,note});if(!result?.ok||result.run_id!==view.runId||result.item?.id!==item.id||result.item?.acknowledged!==true)throw new Error("Eltérő nyugtázási visszaigazolás.");if(operations===view&&view.identity===operationsIdentity()){if(operationsDrafts.get(key)===note)operationsDrafts.delete(key);showToast("Kezelői nyugtázás rögzítve. A hiba megszűnését a megfigyelő külön állapítja meg.");await loadOperations("refresh");}}
    catch(error){if(operations===view&&view.identity===operationsIdentity()){view.stale=true;view.error=`A nyugtázás visszaigazolása sikertelen (${error.message}). Frissítsd a diagnosztikát; az ismétlés előtt ellenőrizd az eltárolt állapotot.`;renderOperations();}}
    finally{operationsAcknowledging.delete(key);renderOperationsSafety();}
  }
  async function openOperationsEvidence(item,view=operations){
    if(view!==operations||view.identity!==operationsIdentity()||operationsLoading||!view.data.items.includes(item)||!operationsEvidenceValid(item.opening))return;
    const opening=item.opening,target={runId:view.runId,incidentId:item.id,point:{id:opening.transport_sample_id,utc:opening.utc,telemetry_until_id:opening.telemetry_until_id,events_until_id:opening.events_until_id}};transportContextExplicitTarget=target;invalidateTransportContext();await loadTransportContext();if(transportContextExplicitTarget===target&&transportContext?.data)$("transport-context-panel")?.scrollIntoView({behavior:"smooth",block:"start"});
  }
  async function operationsLoop(){
    try{const editing=document.activeElement?.classList.contains("operations-review-note");if(!editing&&!operationsLoading&&operations?.data&&operations.data.current_live&&current?.run_id===operations.runId&&!operationsAcknowledging.size)await loadOperations("refresh");else renderOperationsSafety();}
    finally{setTimeout(operationsLoop,2500);}
  }
  function installOperations(){
    if(!$("operations-panel"))return;operationsObservedRun=current?.run_id||null;$("operations-run")?.addEventListener("change",changeOperationsSelection);$("operations-filter")?.addEventListener("change",changeOperationsSelection);$("operations-refresh")?.addEventListener("click",()=>loadOperations());$("operations-runs-refresh")?.addEventListener("click",loadOperationsRuns);$("operations-newer")?.addEventListener("click",()=>loadOperations("newer"));$("operations-older")?.addEventListener("click",()=>loadOperations("older"));$("operations-report-refresh")?.addEventListener("click",loadOperationsReport);renderOperations();loadOperationsRuns();setTimeout(operationsLoop,2500);
    for(const id of ["operations-rule","operations-severity"])$(id)?.addEventListener("change",changeOperationsSelection);
    for(const id of ["operations-car-filter","operations-search"]){$(id)?.addEventListener("input",renderOperationsFilters);$(id)?.addEventListener("keydown",event=>{if(event.key==="Enter"){event.preventDefault();applyOperationsFilters();}});}
    $("operations-filters-apply")?.addEventListener("click",applyOperationsFilters);$("operations-filters-reset")?.addEventListener("click",resetOperationsFilters);
  }
  // This archive keeps a frozen database prefix and its own run selection.
  // Neither live polling nor a new simulation run changes the selected page.
  let transportArchive=null,transportArchiveVersion=0,transportArchiveLoading=false,transportArchiveRunPinned=false;
  let transportContext=null,transportContextVersion=0,transportContextLoading=false,transportContextExplicitTarget=null;
  function transportContextIdentity(){
    if(typeof transportContextExplicitTarget!=="undefined"&&transportContextExplicitTarget){const target=transportContextExplicitTarget;return `incident:${target.runId}:${target.incidentId}:${target.point.id}:${target.point.telemetry_until_id}:${target.point.events_until_id}`;}
    const point=transportArchive?.data.points?.[transportArchive.sampleIndex];return point&&!transportArchiveLoading?`${transportArchive.runId}:${transportArchive.data.until_id}:${point.id}`:null;
  }
  function invalidateTransportContext(){transportContextVersion++;transportContextLoading=false;transportContext=null;}
  function validateTransportContextResponse(result,run,point,carsAfter="",eventsBefore=null){
    const count=value=>Number.isSafeInteger(value)&&value>=0,identifier=value=>typeof value==="string"&&/^[A-Za-z0-9_-]{1,64}$/.test(value);
    if(!result||result.schema_version!==1||result.scope!=="received_evidence_at_transport_sample"||result.run_id!==run||!result.sample||Object.keys(point).some(key=>result.sample[key]!==point[key])||!count(result.telemetry_until_id)||!count(result.events_until_id)||result.telemetry_until_id!==point.telemetry_until_id||result.events_until_id!==point.events_until_id)throw new Error("A megfigyelés vagy a fogadott rekordhatár nem egyezik.");
    const vehicles=result.vehicles,events=result.events;
    if(!vehicles||!Array.isArray(vehicles.items)||vehicles.items.length>25||!count(vehicles.total_count)||typeof vehicles.has_more!=="boolean"||!['trip_samples','legacy_retained_telemetry'].includes(vehicles.source)||typeof vehicles.complete_received_archive!=="boolean"||!count(vehicles.prior_missing_samples_global)||vehicles.complete_received_archive&&(vehicles.source!=="trip_samples"||vehicles.prior_missing_samples_global!==0)||vehicles.items.length>vehicles.total_count||!events||!Array.isArray(events.items)||events.items.length>25||!count(events.total)||events.items.length>events.total)throw new Error("Hiányos vagy túl nagy bizonyítékoldal.");
    let last=carsAfter;for(const car of vehicles.items){if(!identifier(car.car_id)||car.car_id<=last||!Number.isSafeInteger(car.id)||car.id<=0||car.id>result.telemetry_until_id)throw new Error("A járműoldal fogadási határa vagy sorrendje érvénytelen.");last=car.car_id;}
    if(vehicles.has_more?(!vehicles.items.length||vehicles.next_after!==last):vehicles.next_after!==null)throw new Error("A járműlapozás határa érvénytelen.");
    let before=eventsBefore??result.events_until_id+1;for(const event of events.items){if(!Number.isSafeInteger(event.id)||event.id<=0||event.id>=before||event.id>result.events_until_id)throw new Error("Az eseményoldal fogadási határa vagy sorrendje érvénytelen.");before=event.id;}
    if(events.next_before!==null&&(!events.items.length||events.next_before!==before))throw new Error("Az eseménylapozás határa érvénytelen.");
    return result;
  }
  function renderTransportContext(){
    const panel=$("transport-context-panel"),load=$("transport-context-load");if(!panel||!load)return;
    const identity=transportContextIdentity();if(transportContext&&transportContext.identity!==identity)invalidateTransportContext();
    const ids=["transport-context-status","transport-context-note","transport-context-error","transport-context-content","transport-context-cars","transport-context-cars-page","transport-context-retention","transport-context-cars-prev","transport-context-cars-next","transport-context-events","transport-context-events-page","transport-context-events-newer","transport-context-events-older"];if(ids.some(id=>!$(id)))return;
    const context=transportContext,data=context?.data;load.disabled=!identity||transportContextLoading;load.textContent=data?"Kijelölt megfigyelés újratöltése →":"Járművek és események megnyitása →";
    panel.dataset.runId=data?.run_id||"";panel.dataset.sampleId=data?String(data.sample.id):"";panel.dataset.telemetryUntilId=data?String(data.telemetry_until_id):"";panel.dataset.eventsUntilId=data?String(data.events_until_id):"";panel.classList.toggle("is-stale",Boolean(context?.stale));
    const status=$("transport-context-status");status.textContent=transportContextLoading?"Fogadott bizonyíték betöltése…":context?.stale?"Megőrzött oldal · lekérés sikertelen":data?`#${data.sample.id} megfigyelés · rögzített határ`:"Még nincs betöltve";status.className=`data-badge ${context?.stale?"warn":data?"good":"neutral"}`;
    $("transport-context-content").hidden=!data;$("transport-context-error").hidden=!context?.error;$("transport-context-error").textContent=context?.error||"";
    const incidentOrigin=typeof transportContextExplicitTarget!=="undefined"?transportContextExplicitTarget:null;
    $("transport-context-note").textContent=data?`${incidentOrigin?`Incidens #${incidentOrigin.incidentId} · nyitási bizonyíték. `:""}Futás: ${data.run_id} · megfigyelés #${data.sample.id} · rögzítés (UTC): ${data.sample.utc}. Ekkor rendelkezésre álló felső fogadási határ: #${data.telemetry_until_id} telemetria / #${data.events_until_id} esemény. Külön időpontokban fogadott állapotok; az események együtt megjelenése nem igazol okozati kapcsolatot.`:"A megnyitás a megfigyelés eltárolt rekordhatárait használja. Az autók legutóbb fogadott helyzete különböző szimulációs időből származhat; ez nem közös időpillanatra rekonstruált világállapot.";
    if(!data)return;const finite=value=>typeof value==="number"&&Number.isFinite(value),reading=(value,unit,digits=2)=>finite(value)?`${fmt(value,digits)} ${unit}`:"ismeretlen",clock=value=>finite(value)&&value>=0?`${fmt(value,2)} s`:"ismeretlen",cars=data.vehicles,events=data.events;empty($("transport-context-cars"));empty($("transport-context-events"));
    for(const car of cars.items){const row=html("tr",{"data-car-id":car.car_id,"data-receipt-id":car.id}),id=html("td"),state=html("td"),position=html("td"),time=html("td"),action=html("td");
      id.append(html("strong",{},car.car_id),html("small",{},`Telemetria #${car.id}${Number.isSafeInteger(car.seq)&&car.seq>=0?` · sorszám ${car.seq}`:""}`));state.append(html("span",{},stateNames[car.state]||car.state||"Ismeretlen jelentett állapot"),html("small",{},car.wait_reason||"Nincs jelentett várakozási ok"));
      position.append(html("span",{},`x ${reading(car.x,"m")} · y ${reading(car.y,"m")}`),html("small",{},`${reading(car.speed,"m/s")} · ${car.slot_id?`cél ${car.slot_id}`:"nincs jelentett parkoló"}`),html("small",{},car.position_source==="post_step_simulation_reference"?"Modell referenciahelyzete · hibamegfigyelés":car.position_source?`Mentett helyzetforrás: ${car.position_source}`:"Helyzetforrás nincs megadva"));time.append(html("span",{},`Szimuláció: ${clock(car.sim_time)}`),html("small",{},`UTC-fogadás: ${typeof car.utc==="string"?car.utc:"ismeretlen"}`));
      const button=html("button",{type:"button",class:"text-button"},"Út és érzékelők →");button.disabled=transportContextLoading;button.title=`Pontos fogadott telemetria #${car.id}; csak a kijelölt megfigyelésig rendelkezésre álló minták.`;button.addEventListener("click",()=>inspectTransportContextCar(car,context));action.append(button);row.append(id,state,position,time,action);$("transport-context-cars").append(row);
    }
    if(!cars.items.length){const row=html("tr");row.append(html("td",{colspan:5},"E határig nincs megőrzött járműállapot. A hiány nem álló vagy eltűnt autót jelent."));$("transport-context-cars").append(row);}
    $("transport-context-cars-page").textContent=`${cars.items.length} jármű ezen az oldalon · ${cars.total_count} a fogadott előzményben`;$("transport-context-retention").textContent=cars.complete_received_archive?"A megőrzött kompakt fogadási archívumból. A helyzet a legutóbb fogadott keret jelentése; a megfigyelés UTC-idejére nem számolunk új pozíciót.":`Hiányos korábbi megőrzés · ${cars.prior_missing_samples_global} korábbi minta globálisan nem elérhető. Ez nem a kijelölt autó hiányzó mintáinak száma; csak a megmaradt állapotok láthatók.`;
    $("transport-context-cars-prev").disabled=transportContextLoading||!context.carsStack.length;$("transport-context-cars-next").disabled=transportContextLoading||!cars.has_more;
    for(const event of events.items){const item=html("li",{class:`transport-context-event ${["warning","error"].includes(event.level)?event.level:"info"}`,"data-event-id":event.id}),heading=html("div",{class:"transport-context-event-top"});heading.append(html("strong",{},`#${event.id} · ${event.car_id||"Rendszer"}`),html("span",{},`${event.category||"esemény"} · ${event.event_type||"ismeretlen típus"}`));item.append(heading,html("p",{},event.message||"Nincs szöveges eseményleírás"),html("small",{},`Szimuláció: ${clock(event.sim_time)} · UTC-fogadás: ${typeof event.utc==="string"?event.utc:"ismeretlen"}`));$("transport-context-events").append(item);}
    if(!events.items.length)$("transport-context-events").append(html("li",{class:"transport-context-empty"},"E határig nincs megőrzött szemantikus esemény."));
    $("transport-context-events-page").textContent=`${events.items.length} esemény ezen az oldalon · ${events.total} a fogadott előzményben · legújabb elöl`;$("transport-context-events-newer").disabled=transportContextLoading||!context.eventsStack.length;$("transport-context-events-older").disabled=transportContextLoading||events.next_before===null;
  }
  async function loadTransportContext(mode="first"){
    const identity=transportContextIdentity(),explicit=typeof transportContextExplicitTarget!=="undefined"?transportContextExplicitTarget:null,archive=explicit||transportArchive,point=explicit?explicit.point:archive?.data.points?.[archive.sampleIndex];if(!identity||!point)return;const prior=transportContext?.identity===identity?transportContext:null;
    if(mode==="cars-next"&&!prior?.data?.vehicles.has_more||mode==="cars-prev"&&!prior?.carsStack.length||mode==="events-older"&&prior?.data?.events.next_before==null||mode==="events-newer"&&!prior?.eventsStack.length)return;
    const carsStack=mode==="first"?[]:[...(prior?.carsStack||[])],eventsStack=mode==="first"?[]:[...(prior?.eventsStack||[])];let carsAfter=mode==="first"?"":prior?.carsAfter||"",eventsBefore=mode==="first"?null:prior?.eventsBefore??null;
    if(mode==="cars-next"){carsStack.push(carsAfter);carsAfter=prior.data.vehicles.next_after;}if(mode==="cars-prev")carsAfter=carsStack.pop();if(mode==="events-older"){eventsStack.push(eventsBefore);eventsBefore=prior.data.events.next_before;}if(mode==="events-newer")eventsBefore=eventsStack.pop();
    const version=++transportContextVersion;transportContextLoading=true;if(!prior)transportContext={identity,data:null,carsAfter:"",carsStack:[],eventsBefore:null,eventsStack:[],stale:false,error:""};else{prior.error="";prior.stale=false;}renderTransportContext();
    const query=new URLSearchParams({run_id:archive.runId,sample_id:String(point.id),cars_after:carsAfter,cars_limit:"25",events_limit:"25"});if(eventsBefore!==null)query.set("events_before",String(eventsBefore));
    try{const result=await getJSON(`/api/transport/context?${query}`,30000);if(version!==transportContextVersion||identity!==transportContextIdentity())return;validateTransportContextResponse(result,archive.runId,point,carsAfter,eventsBefore);transportContext={identity,data:result,carsAfter,carsStack,eventsBefore,eventsStack,stale:false,error:""};}
    catch(error){if(version===transportContextVersion&&identity===transportContextIdentity()){transportContext.stale=Boolean(transportContext.data);transportContext.error=`A fogadott bizonyíték lekérése sikertelen (${error.message}).${transportContext.data?" A megőrzött oldal a kijelölt megfigyelés korábbi, változatlan rekordhatárához tartozik.":" Nem állítunk elő hiányzó állapotot."}`;}}
    finally{if(version===transportContextVersion){transportContextLoading=false;renderTransportContext();}}
  }
  async function inspectTransportContextCar(car,context=transportContext){
    if(!context?.data||transportContext!==context||context.identity!==transportContextIdentity()||transportContextLoading||!context.data.vehicles.items.some(item=>item===car))return;
    const {run_id:run,sample,telemetry_until_id:untilId,events_until_id:eventsUntilId}=context.data;
    invalidateTripSelection();loadTripRuns.generation=(loadTripRuns.generation||0)+1;catalogueVersion++;catalogueCursor=null;clearTimeout(tripSearchTimer);tripRunPinned=true;
    const runSelect=$("trip-run"),carSelect=$("trip-car");if(![...runSelect.options].some(option=>option.value===run))runSelect.add(new Option(`Rögzített megfigyelés futása · ${run}`,run));runSelect.value=run;$("trip-search").value=car.car_id;carSelect.replaceChildren(new Option(`${car.car_id} · megfigyelésig fogadott állapot`,car.car_id));carSelect.value=car.car_id;
    const valid=()=>transportContext===context&&context.identity===transportContextIdentity()&&tripRunId()===run&&carSelect.value===car.car_id;
    const result=await loadTripWindow({run,car:car.car_id,direction:"end",untilId,eventsUntilId,expectedReceiptId:car.id,contextOrigin:{runId:run,sampleId:sample.id,sampleUtc:sample.utc,telemetryUntilId:untilId,eventsUntilId},selectionGuard:valid});
    if(result&&valid()&&trip?.contextOrigin?.sampleId===sample.id)$("trip-panel").scrollIntoView({behavior:"smooth",block:"start"});
  }
  function transportArchiveChartSegments(history,field,rates){
    const valid=value=>typeof value==="number"&&Number.isFinite(value)&&value>=0,segments=[];let active=[],previous=null;
    const finish=()=>{if(active.length)segments.push(active);active=[];};
    for(const point of history){
      if(!point||!Number.isSafeInteger(point.id)||point.id<=0||!Number.isSafeInteger(point.segment)||point.segment<0||!valid(point.elapsed_s)){finish();previous=null;continue;}
      if(previous&&(point.id<=previous.id||point.segment!==previous.segment||point.elapsed_s<=previous.elapsed_s||point.elapsed_s-previous.elapsed_s>1.5))finish();
      previous=point;const value=point[field],usable=valid(value)&&(rates?valid(point.window_s)&&point.window_s>=1:Number.isSafeInteger(value));
      if(!usable){finish();continue;}active.push({elapsed_s:point.id,value});
    }
    finish();return segments;
  }
  function validateTransportArchiveResponse(result,run,after,until){
    const count=value=>Number.isSafeInteger(value)&&value>=0;
    if(!result||result.schema_version!==1||result.clock!=="host_perf_counter"||result.scope!=="recorded_host_clock_transport_observations"||result.run_id!==run||typeof result.available!=="boolean"||!["recorded","empty","not_recorded"].includes(result.availability)||!count(result.until_id)||!count(result.total_count)||!Array.isArray(result.points)||result.points.length>120||typeof result.has_more!=="boolean")throw new Error("Hiányos vagy eltérő futású forgalmi archívum.");
    if(until!==null&&result.until_id!==until)throw new Error("A rögzített mintahatár megváltozott.");
    let previous=after;for(const point of result.points){if(!point||!Number.isSafeInteger(point.id)||point.id<=previous||point.id>result.until_id)throw new Error("Érvénytelen archivált mintasorrend.");previous=point.id;}
    if(result.has_more&&(!result.points.length||result.next_after!==previous||previous<=after))throw new Error("Érvénytelen archívumlapozási határ.");
    if(!result.available&&(result.availability!=="not_recorded"||result.points.length||result.total_count||result.has_more)||result.available&&result.availability==="not_recorded")throw new Error("Ellentmondó archívumelérhetőség.");
    return result;
  }
  function transportArchiveExportURL(format){
    if(!transportArchive?.data.available)return "";
    return `/api/transport/export?run_id=${encodeURIComponent(transportArchive.runId)}&until_id=${transportArchive.data.until_id}&format=${format}`;
  }
  function renderTransportArchive(){
    const panel=$("transport-archive-panel");if(!panel)return;
    const ids=["transport-archive-status","transport-archive-note","transport-archive-count","transport-archive-segments","transport-archive-peak-rate","transport-archive-peak-queue","transport-archive-range","transport-archive-page","transport-archive-prev","transport-archive-next","transport-archive-load","transport-archive-csv","transport-archive-json","transport-archive-slider","transport-archive-selected-label","transport-archive-detail","transport-archive-rate-chart","transport-archive-queue-chart"];
    if(ids.some(id=>!$(id)))return;
    const archive=transportArchive,data=archive?.data,points=data?.points||[],summary=data?.summary||{},known=Boolean(data?.available),valid=value=>typeof value==="number"&&Number.isFinite(value)&&value>=0,count=value=>Number.isSafeInteger(value)&&value>=0;
    panel.dataset.runId=archive?.runId||$("transport-archive-run")?.value||"";panel.dataset.untilId=data?String(data.until_id):"";panel.classList.toggle("is-stale",Boolean(archive?.stale));
    const badge=$("transport-archive-status");badge.textContent=transportArchiveLoading?"Archívum betöltése…":archive?.stale?"Utolsó betöltés · frissítés sikertelen":!data?"Válassz futást":data.availability==="not_recorded"?"Korábbi futás · nincs forgalmi archívum":data.availability==="empty"?"Még nincs rögzített megfigyelés":"Rögzített előzmény";badge.className=`data-badge ${archive?.stale?"warn":known&&points.length?"good":"neutral"}`;
    $("transport-archive-count").textContent=known&&count(summary.sample_count)?summary.sample_count.toLocaleString("hu-HU"):"—";
    $("transport-archive-segments").textContent=known&&count(summary.segment_count)?`${summary.segment_count} mérési sorozat · #${data.until_id} mintahatárig`:"Az adott verzió nem tárolt ilyen megfigyelést";
    $("transport-archive-peak-rate").textContent=known&&valid(summary.peak_arrival_rate_hz)?fmt(summary.peak_arrival_rate_hz,1):"—";
    $("transport-archive-peak-queue").textContent=known&&count(summary.peak_queue_messages)?summary.peak_queue_messages.toLocaleString("hu-HU"):"—";
    $("transport-archive-range").textContent=known&&summary.first_utc&&summary.last_utc?`Szerveroldali rögzítés (UTC): ${summary.first_utc} → ${summary.last_utc}. ${count(summary.known_queue_count)?summary.known_queue_count:"—"} ismert sorhossz, ${count(summary.valid_rate_count)?summary.valid_rate_count:"—"} értelmezhető rátaablak.`:data?.availability==="not_recorded"?"Ebben a korábbi futásban nem készült tartós forgalmi mérési archívum; a hiány nem nulla forgalmat jelent.":"Még nincs eltárolt rögzítési időtartomány.";
    $("transport-archive-note").textContent=data?`Kiválasztott futás: ${archive.runId} · felső mintahatár: #${data.until_id}. Az új MQTT-megfigyelések nem módosítják ezt a rögzített előzményt. Az „Első oldal / friss rögzítés” új határt kér; új élő futás indítása megtartja a kiválasztást.`:"Az archívum az élő nézettől független. Válassz futást, majd töltsd be a rögzített előzmény első oldalát.";
    $("transport-archive-page").textContent=points.length?`#${points[0].id}–#${points.at(-1).id} · ${points.length} pont ezen az oldalon · ${data.total_count} a rögzített előzményben`:"Legfeljebb 120 pont / oldal · nincs betöltött megfigyelés";
    $("transport-archive-prev").disabled=transportArchiveLoading||!archive?.stack.length;$("transport-archive-next").disabled=transportArchiveLoading||!data?.has_more;$("transport-archive-load").disabled=transportArchiveLoading||!$("transport-archive-run")?.value;
    for(const format of ["csv","json"]){const link=$("transport-archive-"+format),url=transportArchiveExportURL(format);link.setAttribute("aria-disabled",String(!url));if(url){link.setAttribute("href",url);link.setAttribute("download","");}else{link.removeAttribute("href");link.removeAttribute("download");}}
    const options={runId:archive?.runId||"",stale:Boolean(archive?.stale),archive:true};
    drawCapacityChart($("transport-archive-rate-chart"),points,[{field:"arrival_rate_hz",label:"Beérkezés",color:"#188e99"},{field:"dequeue_rate_hz",label:"Feldolgozás",color:"#8263b9",dash:"5 3"}],{...options,unit:"üzenet/s",rates:true,height:170});
    drawCapacityChart($("transport-archive-queue-chart"),points,[{field:"queue_messages",label:"Várakozó sor",color:"#b98a42"}],{...options,unit:"üzenet",rates:false,height:110});
    const slider=$("transport-archive-slider");slider.max=String(Math.max(0,points.length-1));slider.disabled=!points.length||transportArchiveLoading;slider.value=String(archive?.sampleIndex||0);renderTransportArchiveSample();
  }
  function renderTransportArchiveSample(){
    const host=$("transport-archive-detail"),label=$("transport-archive-selected-label");if(!host||!label)return;empty(host);
    const point=transportArchive?.data.points?.[transportArchive.sampleIndex];
    if(typeof renderTransportContext==="function")renderTransportContext();
    if(!point){label.textContent="—";host.append(html("div",{},"Még nincs kijelölt megfigyelés."));return;}
    const valid=value=>typeof value==="number"&&Number.isFinite(value)&&value>=0,count=value=>Number.isSafeInteger(value)&&value>=0,numeric=(value,unit,digits=2)=>valid(value)?`${fmt(value,digits)} ${unit}`:"ismeretlen",integer=value=>count(value)?String(value):"ismeretlen",rate=value=>valid(point.window_s)&&point.window_s>=1?numeric(value,"üzenet/s",1):"nincs értelmezhető ablak";
    label.textContent=`#${point.id} · ${integer(point.segment)}. mérési sorozat`;
    const fields=[["Szerveroldali rögzítés (UTC)",typeof point.utc==="string"?point.utc:"ismeretlen"],["Monoton idő a saját sorozatban",numeric(point.elapsed_s,"s")],["Beérkezés / feldolgozás",`${rate(point.arrival_rate_hz)} / ${rate(point.dequeue_rate_hz)}`],["Sorhossz / rátaablak",`${integer(point.queue_messages)} üzenet / ${numeric(point.window_s,"s")}`],["Utolsó feldolgozott állapot szimulációs ideje",numeric(point.sim_time,"s")],["Állapot kora a megfigyeléskor",numeric(point.snapshot_age_s,"s")],["Fogadott / sorból kivett számláló",`${integer(point.received_total)} / ${integer(point.dequeued_total)}`],["Telemetria felső rekordhatára",`#${integer(point.telemetry_until_id)}`],["Események felső rekordhatára",`#${integer(point.events_until_id)}`]];
    for(const [title,value]of fields){const row=html("div");row.append(html("dt",{},title),html("dd",{},value));host.append(row);}
  }
  async function loadTransportArchive(mode="first"){
    const selector=$("transport-archive-run");if(!selector||!selector.value)return;const run=selector.value,prior=transportArchive?.runId===run?transportArchive:null;
    if(mode==="next"&&!prior?.data.has_more||mode==="prev"&&!prior?.stack.length)return;
    const version=++transportArchiveVersion,stack=mode==="first"?[]:[...prior.stack],after=mode==="next"?prior.data.next_after:mode==="prev"?stack.pop():0,until=mode==="first"?null:prior.data.until_id;
    if(mode==="next")stack.push(prior.after);transportArchiveLoading=true;renderTransportArchive();
    const errorHost=$("transport-archive-error");if(errorHost){errorHost.textContent="";errorHost.hidden=true;}
    try{const result=await getJSON(`/api/transport/history?run_id=${encodeURIComponent(run)}&after=${after}&limit=120${until===null?"":`&until_id=${until}`}`);
      if(version!==transportArchiveVersion||selector.value!==run)return;validateTransportArchiveResponse(result,run,after,until);
      transportArchive={runId:run,data:result,after,stack,sampleIndex:mode==="prev"?Math.max(0,result.points.length-1):0,stale:false};
    }catch(error){if(version===transportArchiveVersion&&selector.value===run){if(transportArchive?.runId===run)transportArchive.stale=true;if(errorHost){errorHost.textContent=`Az archívum betöltése sikertelen (${error.message}). A megőrzött oldal és export a korábbi rögzített határhoz tartozik.`;errorHost.hidden=false;}}}
    finally{if(version===transportArchiveVersion){transportArchiveLoading=false;renderTransportArchive();}}
  }
  function changeTransportArchiveRun(){
    transportContextExplicitTarget=null;invalidateTransportContext();transportArchiveRunPinned=true;transportArchiveVersion++;transportArchiveLoading=false;transportArchive=null;renderTransportArchive();loadTransportArchive();
  }
  async function loadTransportArchiveRuns(){
    const selector=$("transport-archive-run");if(!selector)return;const generation=loadTransportArchiveRuns.generation=(loadTransportArchiveRuns.generation||0)+1;
    try{const result=await getJSON("/api/runs?limit=200"),runs=[...(result.runs||[])];let cursor=result.next_before;
      while(cursor){const page=await getJSON(`/api/runs?limit=200&before=${encodeURIComponent(cursor)}`);if(generation!==loadTransportArchiveRuns.generation)return;runs.push(...(page.runs||[]));if(page.next_before===cursor)break;cursor=page.next_before;}
      if(generation!==loadTransportArchiveRuns.generation)return;const prior=transportArchive?.runId||(transportArchiveRunPinned?selector.value:result.current_run_id);selector.replaceChildren();
      for(const run of runs){if(typeof run.run_id!=="string")continue;selector.add(new Option(`${run.active?"Élő":"Archivált"} · ${run.started_at_utc||run.run_id} · mag ${run.seed??"—"} · ${run.run_id.slice(0,8)}`,run.run_id));}
      if(prior&&!Array.from(selector.options).some(option=>option.value===prior))selector.add(new Option(`Kiválasztott futás · ${prior}`,prior));
      if(prior)selector.value=prior;else if(result.current_run_id)selector.value=result.current_run_id;
      if(!selector.options.length)selector.add(new Option("Még nincs mentett futás",""));renderTransportArchive();
      if(!transportArchive&&!transportArchiveLoading&&selector.value)loadTransportArchive();
    }catch(error){if(generation===loadTransportArchiveRuns.generation){const note=$("transport-archive-note");if(note)note.textContent=`A futáslista frissítése sikertelen (${error.message}); a kiválasztott rögzített előzmény megmarad.`;}}
  }
  function installTransportArchive(){
    if(!$("transport-archive-panel"))return;
    $("transport-archive-run")?.addEventListener("change",changeTransportArchiveRun);$("transport-archive-load")?.addEventListener("click",()=>{transportContextExplicitTarget=null;invalidateTransportContext();loadTransportArchive();});$("transport-archive-runs-refresh")?.addEventListener("click",()=>loadTransportArchiveRuns());$("transport-archive-prev")?.addEventListener("click",()=>{transportContextExplicitTarget=null;invalidateTransportContext();loadTransportArchive("prev");});$("transport-archive-next")?.addEventListener("click",()=>{transportContextExplicitTarget=null;invalidateTransportContext();loadTransportArchive("next");});
    $("transport-archive-slider")?.addEventListener("input",()=>{if(!transportArchive)return;const value=Number($("transport-archive-slider").value);if(Number.isInteger(value)&&value>=0&&value<transportArchive.data.points.length){transportContextExplicitTarget=null;invalidateTransportContext();transportArchive.sampleIndex=value;renderTransportArchiveSample();}});
    if(typeof loadTransportContext==="function"){for(const [id,mode]of [["transport-context-load","first"],["transport-context-cars-prev","cars-prev"],["transport-context-cars-next","cars-next"],["transport-context-events-newer","events-newer"],["transport-context-events-older","events-older"]])$(id)?.addEventListener("click",()=>loadTransportContext(mode));}
    renderTransportArchive();loadTransportArchiveRuns();
  }
  function recoveryControlAllowed(action,data={},state=current){
    const transport=state?.mqtt?.transport,recovery=state?.control_recovery,snapshotAge=transport?.last_snapshot_age_s;
    if(!serverReachable||state?.connected!==false||transport?.broker_connected!==true||transport.communication_fault_active===true||typeof snapshotAge!=="number"||!Number.isFinite(snapshotAge)||snapshotAge<0||snapshotAge>.75)return false;
    return action==="pause"&&recovery?.pause_available===true||action==="speed"&&data?.value===1&&recovery?.slow_available===true;
  }
  function togglePauseRequest(){
    return control(current?.connected===true&&current?.paused?"resume":"pause");
  }
  function renderCarDetails() {
    const selected=Boolean(selectedCar);$("car-empty").hidden=selected;$("car-details-content").hidden=!selected;$("selected-car-id").textContent=selectedCar||"Kijelölt jármű";
    const badge=$("car-panel-state");if(!selected){badge.textContent="Nincs kijelölés";badge.className="data-badge neutral";return;}
    const car=(current?.cars||[]).find(c=>c.id===selectedCar);
    const stale=!serverReachable||!current?.connected;
    badge.textContent=car?(stale?"Utolsó ismert: ":"")+displayState(car):carData?"Archivált jármű":"Adatok betöltése";
    badge.className=`data-badge ${!car||stale?"neutral":/stop|frozen|waiting/.test(car.state)?"warn":"good"}`;
    $("car-speed").textContent=car?reading(car.speed,"m/s"):"—";$("car-slot").textContent=car?.slot_id||"—";
    $("car-elapsed").textContent=reading(car?.elapsed_s);$("car-yard-elapsed").textContent=reading(car?.yard_elapsed_s);
    $("car-sensor-age").textContent=reading(car?.sensor_age_s);const sensor=$("car-sensor-state"),sensorFresh=car?.sensor_ok===true&&!car?.sensors?.fault_active&&!stale;sensor.textContent=car?.sensors?.fault_active?"Érzékelőhiba":stale&&hasValue(car?.sensor_age_s)?"Utolsó ismert":sensorFresh?"Friss":car?.sensor_ok===false?"Elavult":hasValue(car?.sensor_age_s)?"Koradat elérhető":"Nincs adat";sensor.className=`data-badge ${sensorFresh?"good":car?.sensors?.fault_active||car?.sensor_ok===false?"bad":"neutral"}`;
    $("car-position").textContent=car&&hasValue(car.x)&&hasValue(car.y)?`${fmt(car.x)}; ${fmt(car.y)} m`:"—";
    $("car-wait-reason").textContent=car?.wait_reason||(car?(stateNames[car.state]||car.state):carData?"A jármű már nincs az aktív modellben. A telemetria és a napló az utolsó fogadott adatokat mutatja.":"Fogadott járműadatok betöltése…");
    for(const [id,key]of [["car-reservation","reservation_id"],["car-request","request_id"]]){$(id).textContent=car?.[key]||"—";$(id).title=car?.[key]||"";}
  }
  function renderChart(telemetry) {
    const all=(Array.isArray(telemetry)?telemetry:[]).filter(p=>hasValue(p.sim_time)&&hasValue(p.speed)).sort((a,b)=>a.sim_time-b.sim_time);
    const last=all.at(-1)?.sim_time;const points=all.filter(p=>p.sim_time>=last-60);const grid=$("chart-grid"),labels=$("chart-labels");empty(grid);empty(labels);
    $("car-chart-empty").hidden=points.length>0;$("chart-line").setAttribute("d","");$("chart-area").setAttribute("d","");
    const left=38,right=486,top=17,bottom=118;
    const maxSpeed=Math.max(1,Math.ceil(Math.max(...points.map(p=>number(p.speed)),0)*2)/2),start=points[0]?.sim_time||0,end=Math.max(start+1,number(last,start+1));
    const xx=t=>left+(t-start)/(end-start)*(right-left),yy=v=>bottom-Math.max(0,number(v))/maxSpeed*(bottom-top);
    for(let i=0;i<4;i++){const value=maxSpeed*i/3,y=yy(value);node("line",{x1:left,y1:y,x2:right,y2:y,stroke:"#e6edf2","stroke-width":1,"stroke-dasharray":i?"3 4":""},grid);node("text",{x:left-8,y:y+3,"text-anchor":"end",class:"chart-axis"},labels,fmt(value,1));}
    node("text",{x:left-7,y:10,"text-anchor":"end",class:"chart-axis"},labels,"m/s");
    for(let i=0;i<3;i++){const value=start+(end-start)*i/2;node("text",{x:xx(value),y:141,"text-anchor":i===0?"start":i===2?"end":"middle",class:"chart-axis"},labels,`${fmt(value,1)} s`);}
    if(!points.length){$("chart-caption").textContent="Szimulációs idő [s] · sebesség [m/s] · nincs fogadott minta";return;}
    const deltas=points.slice(1).map((p,i)=>p.sim_time-points[i].sim_time).filter(d=>d>0).sort((a,b)=>a-b);const normal=deltas[Math.floor(deltas.length/2)]||.1;
    const segments=[[]];for(const p of points){const seg=segments.at(-1),previous=seg.at(-1);if(previous&&p.sim_time-previous.sim_time>Math.max(.5,normal*5))segments.push([]);segments.at(-1).push(p);}
    const line=segments.map(segment=>segment.map((p,i)=>`${i?"L":"M"}${xx(p.sim_time).toFixed(2)},${yy(p.speed).toFixed(2)}`).join(" ")).join(" ");
    const area=segments.map(segment=>`M${xx(segment[0].sim_time)},${bottom} `+segment.map(p=>`L${xx(p.sim_time)},${yy(p.speed)}`).join(" ")+` L${xx(segment.at(-1).sim_time)},${bottom} Z`).join(" ");
    $("chart-line").setAttribute("d",line);$("chart-line").setAttribute("stroke",colorFor(selectedCar));$("chart-area").setAttribute("d",area);node("circle",{cx:xx(points.at(-1).sim_time),cy:yy(points.at(-1).speed),r:3,fill:colorFor(selectedCar),stroke:"white","stroke-width":1.3},labels);
    $("chart-caption").textContent=`${points.length} fogadott minta · ${fmt(end-start)} s időtáv · legfeljebb az utolsó 60 szimulációs másodperc`;
  }
  function renderCarHistory(events) {const host=$("car-history");empty(host);for(const e of [...(events||[])].sort((a,b)=>number(b.id)-number(a.id)).slice(0,3)){const row=html("div",{class:`mini-event ${e.level||"info"}`});row.append(html("time",{},timeText(e.sim_time)),html("span",{},e.message||e.event_type||"Esemény"));host.append(row);}}
  async function carLoop() {
    const id=selectedCar,run=observedRun,version=carVersion;
    if(id&&serverReachable&&!carBusy){carBusy=true;try{const result=await getJSON(`/api/car?car_id=${encodeURIComponent(id)}`);if(version===carVersion&&run===observedRun&&result.run_id===run&&id===selectedCar){carData=result;renderCarDetails();renderChart(result.telemetry);renderCarHistory(result.events);}}
      catch(error){if(version===carVersion&&run===observedRun){$("chart-caption").textContent=`A telemetria-idősor még nem érhető el (${error.message}). A térkép utolsó MQTT-adatai megmaradnak.`;}}
      finally{carBusy=false;}}
    setTimeout(carLoop,1000);
  }
  // The trip archive has its own run identity, geometry and clock. Live polling
  // never moves its recorded marker or replaces a previous run's map.
  let trip=null,tripVersion=0,tripLoading=false,catalogueVersion=0,catalogueCursor=null,tripSearchTimer=null,tripRunPinned=false;
  function tripError(message=""){$("trip-error").textContent=message;$("trip-error").hidden=!message;}
  function stopTrip(message=null){
    if(trip){const active=trip.playing||trip.continuing||trip.pendingWindow;trip.playGeneration=(trip.playGeneration||0)+1;trip.playing=false;delete trip.pendingWindow;
      if(trip.continuing){tripVersion++;tripLoading=false;trip.continuing=false;renderTripNavigation();}
      if(active){$("trip-status").textContent=message||"Visszajátszás szünetel";$("trip-status").className=`data-badge ${message?.includes("Idő")?"warn":"neutral"}`;}}
    $("trip-play").textContent="Visszajátszás";
  }
  function tripQuery(run,car){return `run_id=${encodeURIComponent(run)}&car_id=${encodeURIComponent(car)}`;}
  function tripRunId(){return $("trip-run").value||current?.run_id||observedRun;}
  async function loadTripRuns(){
    const version=loadTripRuns.generation=(loadTripRuns.generation||0)+1;
    try{const data=await getJSON("/api/runs?limit=200"),runs=[...(data.runs||[])];let cursor=data.next_before;while(cursor){const page=await getJSON(`/api/runs?limit=200&before=${encodeURIComponent(cursor)}`);if(version!==loadTripRuns.generation)return;runs.push(...(page.runs||[]));if(page.next_before===cursor)break;cursor=page.next_before;}
      if(version!==loadTripRuns.generation)return;const selector=$("trip-run"),before=selector.value,prior=trip?.runId||(tripRunPinned?before||data.current_run_id:data.current_run_id);selector.replaceChildren();
      for(const run of runs){const label=`${run.active?"Élő":"Archivált"} · ${run.started_at_utc||run.run_id} · mag ${run.seed??"—"} · ${run.run_id.slice(0,8)}`;selector.add(new Option(label,run.run_id));}
      if([...selector.options].some(o=>o.value===prior))selector.value=prior;else if(data.current_run_id)selector.value=data.current_run_id;
      if(!selector.options.length)selector.add(new Option("Még nincs mentett futás",""));
      if(before!==selector.value){catalogueVersion++;catalogueCursor=null;$("trip-car").replaceChildren(new Option("Válassz mentett járművet",""));if(trip&&trip.runId!==selector.value)invalidateTripSelection();}
    }catch(error){if(version===loadTripRuns.generation)$("trip-catalogue-note").textContent=`A futásarchívum betöltésére várunk (${error.message}).`;}
  }
  async function loadTripCars(more=false){
    const run=tripRunId();if(!run)return;const version=++catalogueVersion,prior=$("trip-car").value,search=$("trip-search").value.trim(),before=more?catalogueCursor:null;
    try{const data=await getJSON(`/api/cars?run_id=${encodeURIComponent(run)}&search=${encodeURIComponent(search)}&limit=100${before?`&before=${encodeURIComponent(before)}`:""}`);if(version!==catalogueVersion||run!==tripRunId()||search!==$("trip-search").value.trim())return;
      if(!more)$("trip-car").replaceChildren(new Option("Válassz mentett járművet",""));
      const present=new Set([...$("trip-car").options].map(o=>o.value));for(const car of data.cars||[]){const id=car.id||car.car_id;if(id&&!present.has(id))$("trip-car").add(new Option(`${id} · ${car.active?"aktív":stateNames[car.last_state]||car.last_state||"archivált"} · ${number(car.sample_count).toLocaleString("hu-HU")} minta`,id));}
      if([...$("trip-car").options].some(o=>o.value===prior))$("trip-car").value=prior;else if(prior&&trip?.runId===run&&trip?.carId===prior)invalidateTripSelection();
      catalogueCursor=data.next_before||null;$("trip-more-cars").hidden=!catalogueCursor;
      $("trip-catalogue-note").textContent=`${number(data.total,(data.cars||[]).length).toLocaleString("hu-HU")} mentett jármű a kijelölt futásban · ${Math.max(0,$("trip-car").options.length-1)} látható a listában. Az azonosító alapján kereshetsz.`;
    }catch(error){if(version===catalogueVersion)tripError(`A járműlista nem tölthető be: ${error.message}`);}
  }
  function tripClockProblem(a,b=null){
    const clock=p=>typeof p?.sim_time==="number"&&Number.isFinite(p.sim_time)&&p.sim_time>=0;
    if(!clock(a)||b&&!clock(b))return "Időbélyeghiba: hiányzó vagy érvénytelen szimulációs idő";
    if(b&&Number(b.sim_time)<Number(a.sim_time))return "Időrendi törés: a szimulációs idő visszalép";
    return null;
  }
  function timestampGap(a,b,summary){
    const period=number([b.period_s,b.details?.period_s,b.sensors?.period_s,a.period_s,a.details?.period_s,a.sensors?.period_s,summary?.sample_period_s].find(value=>hasValue(value)&&Number(value)>0),.1);
    return Boolean(tripClockProblem(a,b))||Boolean(b.break_before)||Boolean(b.gap_before)||number(b.time_gap_before_s)>0||(hasValue(a.seq)&&hasValue(b.seq)&&b.seq>a.seq+1)||number(b.sim_time)-number(a.sim_time)>Math.max(.15,period*1.55);
  }
  function tripSegments(samples,summary){const segments=[];let active=[];for(const sample of samples){if(!hasValue(sample.x)||!hasValue(sample.y)){if(active.length)segments.push(active);active=[];continue;}if(active.length&&timestampGap(active.at(-1),sample,summary)){segments.push(active);active=[];}active.push(sample);}if(active.length)segments.push(active);return segments;}
  function tripOverviewSegments(points){
    const segments=[];let active=[];for(const point of points){if(!hasValue(point.x)||!hasValue(point.y)){if(active.length)segments.push(active);active=[];continue;}if(point.break_before&&active.length){segments.push(active);active=[];}active.push(point);}if(active.length)segments.push(active);return segments;
  }
  function tripExtentPoints(samples,overview){
    const points=[...samples,...(overview?.points||[])].filter(p=>hasValue(p.x)&&hasValue(p.y)).map(p=>[Number(p.x),Number(p.y)]),b=overview?.bounds;
    if(b&&[b.min_x,b.min_y,b.max_x,b.max_y].every(hasValue)&&Number(b.min_x)<=Number(b.max_x)&&Number(b.min_y)<=Number(b.max_y))points.push([Number(b.min_x),Number(b.min_y)],[Number(b.max_x),Number(b.max_y)]);
    return points;
  }
  function rebuildTripMap(){
    for(const id of ["trip-map-base","trip-trail-layer","trip-point-layer","trip-label-layer"])empty($(id));if(!trip)return;
    const positions=trip.samples.filter(s=>hasValue(s.x)&&hasValue(s.y)),hasMap=Boolean(trip.map);
    const geometry=hasMap?logisticsGeometry(trip.map):{buildings:[],roads:[],crossings:[],slots:[],parking_areas:[],production_exits:[],zones:[]};
    const points=tripExtentPoints(positions,trip.overview);let bounds=geometry.bounds;
    if(points.length){const extents=points.reduce((b,p)=>({minX:Math.min(b.minX,p[0]),minY:Math.min(b.minY,p[1]),maxX:Math.max(b.maxX,p[0]),maxY:Math.max(b.maxY,p[1])}),{minX:Infinity,minY:Infinity,maxX:-Infinity,maxY:-Infinity});const{minX,minY,maxX,maxY}=extents;bounds=bounds?{x:Math.min(bounds.x,minX-12),y:Math.min(bounds.y,minY-12),w:Math.max(bounds.x+bounds.w,maxX+12)-Math.min(bounds.x,minX-12),h:Math.max(bounds.y+bounds.h,maxY+12)-Math.min(bounds.y,minY-12)}:{x:minX-15,y:minY-15,w:Math.max(40,maxX-minX+30),h:Math.max(40,maxY-minY+30)};}
    bounds??={x:0,y:0,w:140,h:90};trip.bounds=bounds;trip.view={...bounds};trip.follow=false;
    const base=$("trip-map-base"),labels=$("trip-label-layer");tripMapDefinitions(base);
    node("rect",{x:bounds.x,y:bounds.y,width:bounds.w,height:bounds.h,class:"map-background"},base);
    node("rect",{x:bounds.x,y:bounds.y,width:bounds.w,height:bounds.h,fill:"url(#trip-map-grid)"},base);
    const layers=Object.fromEntries(["buildings","roads","zones","crossings","parking"].map(name=>[name,node("g",{class:`trip-${name}-layer`},base)]));
    layers.annotations=labels;trip.parkingLayer=layers.parking;
    if(hasMap)renderMapGeometry(geometry,layers,{roofId:"trip-factory-roof"});
    trip.overviewLayer=node("g",{class:"trip-overview-layer","aria-label":"A teljes rögzített út ritkított áttekintése"},$("trip-trail-layer"));
    for(const segment of tripOverviewSegments(trip.overview?.points||[])){if(segment.length===1)node("circle",{cx:segment[0].x,cy:segment[0].y,r:.65,fill:"#8797a8",class:"trip-overview-line"},trip.overviewLayer);else node("polyline",{points:segment.map(p=>`${p.x},${p.y}`).join(" "),fill:"none",stroke:"#8797a8","stroke-width":1.6,"vector-effect":"non-scaling-stroke",class:"trip-overview-line"},trip.overviewLayer);}
    renderTripOverview();
    trip.sensorLayer=node("g",{class:"trip-sensors-layer","aria-label":"A kijelölt mentett minta érzékelőmegfigyelései"},$("trip-trail-layer"));
    const segments=tripSegments(trip.samples,trip.summary);trip.segments=segments;const stride=Math.max(1,Math.ceil(positions.length/20000));trip.drawStride=stride;
    for(const segment of segments){const visible=segment.filter((s,i)=>i===0||i===segment.length-1||i%stride===0);if(visible.length===1)node("circle",{cx:visible[0].x,cy:visible[0].y,r:.8,fill:colorFor(trip.carId)},$("trip-trail-layer"));else node("polyline",{points:visible.map(p=>`${p.x},${p.y}`).join(" "),fill:"none",stroke:colorFor(trip.carId),"stroke-width":2.2,"stroke-linejoin":"round","stroke-linecap":"round",opacity:.72,"vector-effect":"non-scaling-stroke",class:"observed-trip-line"},$("trip-trail-layer"));}
    $("trip-map-empty").hidden=positions.length>0;$("trip-map-title").textContent=`${trip.carId} · ${trip.runId} · ${hasMap?"a futáshoz mentett térképen":"mentett pozíciók, térképi háttér nélkül"}`;
    const key=document.querySelector(".trip-map-heading .trail-key");if(key)key.style.background=colorFor(trip.carId);
    updateTripView();
  }
  function renderTripOverview(){
    if(!trip)return;const overview=trip.overview;if(trip.overviewLayer)trip.overviewLayer.setAttribute("visibility",$("trip-show-overview").checked?"visible":"hidden");
    $("trip-overview-note").textContent=overview?`${overview.complete_received_archive===false?"Megőrzött, hiányos út":"Teljes rögzített út"}: ${number(overview.point_count,(overview.points||[]).length).toLocaleString("hu-HU")} eredeti pont / ${number(overview.total_count).toLocaleString("hu-HU")} fogadott keret · ${overview.downsampled?`ritkított áttekintés, lépés ${overview.stride}; köztes kanyarok kimaradhatnak`:"minden megjeleníthető mentett pont"}. Szürke: teljes út; színes: betöltött rész. ${number(overview.detected_break_count)} észlelt adatrés / óratörés, ${number(overview.invalid_position_count)} hiányos pozíció.${overview.complete_received_archive===false?" A régi archívum hiányos; korábban törölt pontok nem állíthatók vissza.":""} Az adatréseket és óratöréseket nem kötjük össze; érzékelőminta csak a betöltött ablakból választható.`:"Ehhez az archívumhoz nincs teljesút-áttekintés; a színes út a betöltött ablakot mutatja.";
  }
  function tripMapPixel(){const svg=$("trip-map"),v=trip?.view||trip?.bounds;return v?Math.max(v.w/Math.max(260,svg.clientWidth||800),v.h/Math.max(1,svg.clientHeight||600)):.4;}
  function updateTripView(){
    if(!trip?.bounds)return;const svg=$("trip-map"),v=trip.view||trip.bounds;
    svg.setAttribute("viewBox",`${v.x} ${v.y} ${v.w} ${v.h}`);svg.style.aspectRatio=`${trip.bounds.w}/${trip.bounds.h}`;
    svg.classList.toggle("map-wide",number(trip.map?.width,trip.bounds.w)>250);svg.classList.toggle("map-detail",v.w<240);
    svg.style.setProperty("--map-label-scale",Math.max(.5,v.w/150));svg.style.setProperty("--map-pixel",tripMapPixel());svg.style.setProperty("--trip-pixel",tripMapPixel());
    if($("trip-map-zoom-in"))$("trip-map-zoom-in").disabled=v.w<=30;
    if($("trip-map-zoom-out"))$("trip-map-zoom-out").disabled=v.w>=trip.bounds.w*1.7;
    const point=trip.samples[trip.index];if($("trip-map-focus")){$("trip-map-focus").disabled=!point||!hasValue(point.x)||!hasValue(point.y);$("trip-map-focus").classList.toggle("selected",Boolean(trip.follow));}
    layoutTripParkingLabels();renderTripSensorOverlay(point,Boolean(trip.perceptionGap));
    const label=svg.querySelector(".trip-car-label");if(label)label.setAttribute("y",-number(trip.map?.vehicle_width,1.8)/2-10*tripMapPixel());
  }
  function resizeTripMap(){updateTripView();}
  function layoutTripParkingLabels(){
    if(!trip?.parkingLayer)return;const pixel=tripMapPixel(),activeSlot=trip.samples[trip.index]?.slot_id;
    const labels=[...trip.parkingLayer.querySelectorAll(".slot-group")].map(g=>{const label=g.querySelector(".slot-label"),id=g.dataset.slot,highlighted=id===activeSlot;g.classList.toggle("assigned-trip-slot",highlighted);return {g,x:number(label.getAttribute("x")),y:number(label.getAttribute("y")),width:(id.length*5+6)*pixel,highlighted};}).sort((a,b)=>Number(b.highlighted)-Number(a.highlighted)||a.y-b.y||a.x-b.x);
    const shown=[];for(const label of labels){const overlaps=shown.some(other=>Math.abs(label.y-other.y)<11*pixel&&Math.abs(label.x-other.x)<(label.width+other.width)/2),visible=label.highlighted||!overlaps;label.g.classList.toggle("sparse-slot-label",!visible);label.g.classList.toggle("highlighted-slot-label",label.highlighted);if(visible)shown.push(label);}
  }
  function renderTripSensorOverlay(point,held=false){
    if(!trip?.sensorLayer)return;const layer=trip.sensorLayer;empty(layer);layer.classList.remove("sensor-layer-stale");for(const key of ["carId","sampleTime","fresh"])delete layer.dataset[key];
    const sample=point?.sensors;if(!usableSensorFrame(sample)||!hasValue(sample.pose?.x_m)||!hasValue(sample.pose?.y_m))return;
    const fresh=Boolean(sample.fresh&&!sample.fault_active&&!held);drawMapSensorOverlay(layer,{car:{id:trip.carId},sample,fresh,selectedChannel:selectedProximityChannel("trip",{run_id:trip.runId,car_id:trip.carId,sensors:sample})?.id},tripMapPixel());
  }
  function fitTripMap(){if(!trip?.bounds)return;trip.follow=false;trip.view={...trip.bounds};updateTripView();}
  function zoomTripMap(factor){if(!trip?.bounds)return;trip.follow=false;const v=trip.view||trip.bounds,w=Math.max(30,Math.min(trip.bounds.w*1.8,v.w*factor)),h=w*trip.bounds.h/trip.bounds.w;trip.view={x:v.x+(v.w-w)/2,y:v.y+(v.h-h)/2,w,h};updateTripView();}
  function focusTripMap(){
    const point=trip?.samples[trip.index];if(!point||!hasValue(point.x)||!hasValue(point.y))return;trip.follow=true;
    const w=Math.min(trip.bounds.w,70),h=w*trip.bounds.h/trip.bounds.w;trip.view={x:Number(point.x)-w/2,y:Number(point.y)-h/2,w,h};updateTripView();
  }
  function installTripMapControls(){
    for(const [id,action]of [["trip-map-zoom-in",()=>zoomTripMap(.75)],["trip-map-zoom-out",()=>zoomTripMap(1.3)],["trip-map-fit",fitTripMap],["trip-map-focus",()=>{if(trip?.follow){trip.follow=false;updateTripView();}else focusTripMap();}]])$(id)?.addEventListener("click",action);
    const svg=$("trip-map");let pointer=null;
    svg.addEventListener("wheel",event=>{if(!event.ctrlKey||!trip?.bounds)return;event.preventDefault();zoomTripMap(event.deltaY<0?.88:1.14);},{passive:false});
    svg.addEventListener("pointerdown",event=>{if(!trip?.bounds||event.button!==0)return;const transform=svg.getScreenCTM();if(!transform)return;const matrix=transform.inverse(),screen=svg.createSVGPoint();screen.x=event.clientX;screen.y=event.clientY;pointer={id:event.pointerId,point:screen.matrixTransform(matrix),matrix,view:{...(trip.view||trip.bounds)}};trip.follow=false;svg.setPointerCapture(event.pointerId);});
    svg.addEventListener("pointermove",event=>{if(!pointer||!trip||pointer.id!==event.pointerId)return;const screen=svg.createSVGPoint();screen.x=event.clientX;screen.y=event.clientY;const point=screen.matrixTransform(pointer.matrix);trip.view={...pointer.view,x:pointer.view.x+pointer.point.x-point.x,y:pointer.view.y+pointer.point.y-point.y};updateTripView();});
    const release=()=>{pointer=null;};svg.addEventListener("pointerup",release);svg.addEventListener("pointercancel",release);svg.addEventListener("lostpointercapture",release);
  }
  function nearestTripSlot(point,map){
    if(!map||!hasValue(point?.x)||!hasValue(point?.y))return null;
    let nearest=null,best=5;
    for(const slot of map.slots||[]){
      if(!hasValue(slot.x)||!hasValue(slot.y))continue;
      const distance=Math.hypot(Number(slot.x)-Number(point.x),Number(slot.y)-Number(point.y));
      if(distance<best-1e-9||(nearest&&Math.abs(distance-best)<=1e-9&&slot.id===point.slot_id)){nearest=slot;best=distance;}
    }
    return nearest;
  }
  function tripLocation(point){if(!trip?.map||!hasValue(point.x)||!hasValue(point.y))return "—";const slot=nearestTripSlot(point,trip.map);if(slot)return `${["parked","parking"].includes(point.state)&&slot.id===point.slot_id?"Parkoló":"Parkoló közelében"}: ${slot.id}`;const crossing=(trip.map.crossings||[]).map(c=>({c,distance:Math.hypot(c.x-point.x,c.y-point.y)})).filter(item=>item.distance<9).sort((a,b)=>a.distance-b.distance)[0]?.c;if(crossing)return `Gyalogátkelő: ${crossing.id}`;let closest=null,best=Infinity;for(const road of trip.map.roads||[]){const ps=Array.isArray(road)?road:road.points||[];for(let i=1;i<ps.length;i++){const distance=pointSegmentDistance([point.x,point.y],ps[i-1],ps[i]);if(distance<best){best=distance;closest=road.label||road.id||"Útszakasz";}}}return best<12?`${closest}${hasValue(point.heading)?` · ${fmt(point.heading,0)}°`:" · irányadat nincs mentve"}`:hasValue(point.heading)?`${fmt(point.heading,0)}° irány`:"—";}
  function renderTripDecisions(){
    const panel=$("trip-decisions-panel"),badge=$("trip-decisions-status");
    if(!trip){
      panel.dataset.runId="";panel.dataset.reserve="unknown";
      for(const field of ["sections","minimum","strongest"])$("trip-decisions-"+field).textContent="—";
      for(const field of ["minimum-note","strongest-note","counts"])$("trip-decisions-"+field).textContent="";
      $("trip-decisions-scope").textContent="A betöltött ablak döntéseire várunk.";
      badge.textContent="Nincs betöltött ablak";badge.className="data-badge neutral";
      renderTripDecisionNavigation();return;
    }
    const analysis=window.YardBrakingAnalysis.summarizeWindow(trip.samples,{sample_period_s:trip.summary?.sample_period_s});
    trip.decisionWindow=analysis;panel.dataset.runId=trip.runId;
    const minimum=analysis.minimum_moving_reserve,strongest=analysis.strongest_braking;
    panel.dataset.reserve=minimum?minimum.model_reserve_m<0?"negative":"nonnegative":"unknown";
    $("trip-decisions-sections").textContent=String(analysis.braking_sections.length);
    $("trip-decisions-minimum").textContent=minimum?`${reading(minimum.model_reserve_m,"m",2)} · ${reading(minimum.decision_sim_time_s,"s",2)} →`:"Nincs összevethető döntés";
    $("trip-decisions-strongest").textContent=strongest?`${reading(strongest.applied_acceleration_m_s2,"m/s²",2)} · ${reading(strongest.decision_sim_time_s,"s",2)} →`:"Nincs fékezési döntés";
    $("trip-decisions-minimum-note").textContent=minimum?"Mozgó, konzisztens döntés kijelölt megállási céllal. Kattintás: a pontos fogadott keret.":"Csak a mozgó, konzisztens, megállási céllal rendelkező döntések hasonlíthatók össze.";
    $("trip-decisions-strongest-note").textContent=strongest?"Legnegatívabb naplózott gyorsulás mozgó, konzisztens döntésből. Kattintás: a pontos keret.":"Ebben az ablakban nincs összevethető, mozgás közbeni negatív gyorsulás.";
    $("trip-decisions-counts").textContent=`${analysis.frame_count} fogadott keret · ${analysis.unique_decisions} egyedi döntésidő · ${analysis.duplicate_decisions} azonos döntésidővel ismételt keret · ${analysis.retained_decisions} megőrzöttként is látott és ${analysis.freshness_unknown_decisions} ismeretlen érzékelőfrissességű döntésidő. ${analysis.unavailable_frames} nem értelmezhető keret · ${analysis.invalid_decisions} hibás és ${analysis.inconsistent_decisions} ellentmondó döntésidő; ezek a csoportok átfedhetnek.`;
    $("trip-decisions-scope").textContent=`${trip.carId} · ${trip.runId} · betöltött ablak: ${trip.window?.start_ordinal??"—"}–${trip.window?.end_ordinal??"—"}. A szakaszok a fogadási sorrendben összefüggő fékezési megfigyeléseket jelölik; az adatrés és az óratörés megszakítja őket. Az ablak szélén a fékezés folytatódhat. Teljesúti eseményszámot vagy fékezési időtartamot ebből nem becsülünk. A vezérlési összegzés nem igazol friss érzékelést; a tartalék modelladat.`;
    badge.textContent=analysis.unique_decisions?"Betöltött döntések összesítve":"Nincs azonosítható döntés";
    badge.className=`data-badge ${analysis.inconsistent_decisions||analysis.unavailable_frames?"warn":analysis.unique_decisions?"good":"neutral"}`;
    renderTripDecisionNavigation();
  }
  function renderTripDecisionNavigation(){
    const analysis=trip?.decisionWindow,sections=analysis?.braking_sections||[],index=trip?.index;
    const previous=sections.filter(section=>section.end_index<index).at(-1),next=sections.find(section=>section.start_index>index);
    $("trip-decisions-previous").disabled=tripLoading||!previous;
    $("trip-decisions-next").disabled=tripLoading||!next;
    $("trip-decisions-minimum").disabled=tripLoading||!analysis?.minimum_moving_reserve;
    $("trip-decisions-strongest").disabled=tripLoading||!analysis?.strongest_braking;
    const active=sections.findIndex(section=>section.start_index<=index&&index<=section.end_index),section=sections[active];
    $("trip-decisions-position").textContent=!analysis?"Nincs betöltött döntésablak.":tripLoading?"Ablakbetöltés folyamatban · a korábbi összegzés marad.":section?`${active+1}. fékezési szakasz / ${sections.length} · döntésidők: ${reading(section.start_time_s,"s",2)} → ${reading(section.end_time_s,"s",2)} · ${section.decision_count} különböző döntés${section.starts_at_window_edge||section.ends_at_window_edge?" · ablakhatár érintett":""}`:`A kijelölt keret nem része összevethető fékezési szakasznak. ${sections.length} szakasz kereshető a betöltött ablakban.`;
  }
  function jumpTripDecision(index,identity=trip){
    if(!trip||trip!==identity||tripLoading||!trip.decisionWindow||!Number.isInteger(index)||index<0||index>=trip.samples.length)return;
    stopTrip();renderTripPoint(index);
    const heading=$("trip-perception-title");
    heading.scrollIntoView({behavior:window.matchMedia("(prefers-reduced-motion: reduce)").matches?"instant":"smooth",block:"start"});
    heading.focus({preventScroll:true});
  }
  function stepTripDecision(direction){
    if(!trip||tripLoading||![-1,1].includes(direction))return;
    const sections=trip.decisionWindow?.braking_sections||[],identity=trip;
    const target=direction<0?sections.filter(section=>section.end_index<trip.index).at(-1):sections.find(section=>section.start_index>trip.index);
    if(target)jumpTripDecision(target.start_index,identity);
  }
  function renderTripPoint(index){
    if(!trip)return;trip.perceptionGap=false;trip.index=Math.max(0,Math.min(trip.samples.length-1,number(index)));$("trip-slider").value=trip.index;
    renderTripDecisionNavigation();
    const point=trip.samples[trip.index];empty($("trip-point-layer"));$("trip-sample-index").textContent=point?`${(number(trip.window?.start_ordinal,1)+trip.index).toLocaleString("hu-HU")} / ${number(trip.window?.total_count,trip.samples.length).toLocaleString("hu-HU")}`:"0 minta";
    for(const id of ["trip-start","trip-play","trip-end","trip-slider"])$(id).disabled=tripLoading||!point;
    if(!point){for(const id of ["trip-time","trip-utc","trip-position","trip-speed","trip-state","trip-slot","trip-distance","trip-location"])$(id).textContent="—";$("trip-status").textContent="Nincs fogadott minta";$("trip-status").className="data-badge neutral";renderTripPerception(null);return;}
    $("trip-time").textContent=tripClockProblem(point)?"Nincs érvényes szimulációs idő":`${fmt(point.sim_time,2)} s · ${timeText(point.sim_time)}`;$("trip-utc").textContent=point.utc||point.received_at||"Nincs mentett UTC-adat";$("trip-position").textContent=hasValue(point.x)&&hasValue(point.y)?`${fmt(point.x,2)}; ${fmt(point.y,2)}`:"Nincs pozícióminta";$("trip-speed").textContent=hasValue(point.speed)?fmt(point.speed,2):"—";$("trip-state").textContent=`${stateNames[point.state]||point.state||"—"}${point.wait_reason?` · ${point.wait_reason}`:""}`;$("trip-slot").textContent=point.slot_id||"—";$("trip-distance").textContent=hasValue(point.distance_m)?fmt(point.distance_m,2):"Nem volt mentve";$("trip-location").textContent=tripLocation(point);
    if(hasValue(point.x)&&hasValue(point.y)){
      const car=node("g",{class:"trip-car-group",transform:`translate(${point.x} ${point.y})`},$("trip-point-layer"));
      if(hasValue(point.heading)){
        const {body,shape,selected}=drawMapCar(car,trip.carId,trip.map?.vehicle_length,trip.map?.vehicle_width,"trip-car-shadow");body.setAttribute("transform",`rotate(${Number(point.heading)})`);selected.setAttribute("visibility","visible");
        shape.setAttribute("fill",["communication_stop","sensor_stop","frozen"].includes(point.state)?"#c56c60":point.state==="pedestrian_stop"?"#d3993f":colorFor(trip.carId));
      }else node("circle",{cx:0,cy:0,r:4*tripMapPixel(),fill:colorFor(trip.carId),stroke:"#fff","stroke-width":1,"vector-effect":"non-scaling-stroke"},car);
      node("text",{x:0,y:-number(trip.map?.vehicle_width,1.8)/2-10*tripMapPixel(),class:"car-id trip-car-label"},car,trip.carId);
      node("title",{},car,`${trip.carId}: ${tripClockProblem(point)?"ismeretlen szimulációs idő":reading(point.sim_time,"s",2)} · ${fmt(point.x,2)}; ${fmt(point.y,2)} m${hasValue(point.heading)?` · ${fmt(point.heading,1)}°`:" · irányadat nincs mentve"}`);
    }
    layoutTripParkingLabels();renderTripSensorOverlay(point);if(trip.follow&&hasValue(point.x)&&hasValue(point.y)){const v=trip.view||trip.bounds;trip.view={...v,x:Number(point.x)-v.w/2,y:Number(point.y)-v.h/2};updateTripView();}
    if($("trip-map-context"))$("trip-map-context").textContent=`${tripClockProblem(point)?"Ismeretlen szimulációs idejű":reading(point.sim_time,"s",2)} fogadott járműminta · ${usableSensorFrame(point.sensors)?`érzékelőminta: ${fmt(point.sensors.sample_sim_time_s,2)} s${point.sensors.fault_active||!point.sensors.fresh?" · elavult / hibás megfigyelés":""}`:"ehhez a mintához nincs megjeleníthető érzékelőcsomag"}. A parkolóhelyek szürke színe ismeretlen történeti foglaltságot jelent; a kiemelés az autónak kiosztott hely. A pontok a mentett érzékelőcsomag megfigyelései; élő jármű- vagy gyalogospozíció nem kerül a visszajátszásba.`;
    const previous=trip.samples[trip.index-1],clockProblem=tripClockProblem(point)||(previous?tripClockProblem(previous,point):null),gap=previous&&timestampGap(previous,point,trip.summary);$("trip-status").textContent=clockProblem|| (trip.playing?"Mentett minták visszajátszása":gap?"Adathiány utáni fogadott minta":"Mentett minta");$("trip-status").className=`data-badge ${clockProblem||gap?"warn":"neutral"}`;trip.perceptionGap=false;renderTripPerception(point);
  }
  async function jumpTripEvent(event){if(!trip||typeof event?.sim_time!=="number"||!Number.isFinite(event.sim_time)||event.sim_time<0)return;await loadTripWindow({direction:"time",simTime:event.sim_time,event});}
  function renderTripEvents(){const host=$("trip-events");empty(host);if(!trip)return;const clock=event=>tripClockProblem(event)?null:event.sim_time;
    const sorted=[...trip.events].sort((a,b)=>{const at=clock(a),bt=clock(b);return at===null&&bt!==null?1:bt===null&&at!==null?-1:(at!==null&&bt!==null?at-bt:0)||number(a.id)-number(b.id);});
    for(const event of sorted){const time=clock(event),button=html("button",{type:"button",class:`trip-event ${event.level||"info"}`});button.append(html("time",{},time===null?"Időadat nélkül":`${fmt(time,2)} s`),html("span",{},event.message||event.event_type||"Járműesemény"));button.disabled=time===null;if(event.utc)button.title=`Fogadás: ${event.utc} UTC`;if(time===null)button.title=`${button.title?`${button.title}. `:""}Nincs érvényes szimulációs idő; idő szerinti ugrás nem végezhető.`;button.addEventListener("click",()=>jumpTripEvent(event));host.append(button);}
    if(!sorted.length)host.append(html("p",{class:"trip-note"},"A járműhöz még nincs mentett szemantikus esemény."));$("trip-events-older").hidden=!trip.eventsBefore;$("trip-events-older").disabled=false;$("trip-events-note").textContent=`${sorted.length} betöltött esemény · kattintás: ugrás az eseményt megelőző fogadott mintára${sorted.some(event=>clock(event)===null)?" · az időadat nélküli eseményeknél az ugrás nem érhető el":""}`;
  }
  function renderTripSummary(){
    if(!trip)return;const first=trip.samples[0],last=trip.samples.at(-1),w=trip.window,summary=trip.summary,gaps=Math.max(0,(trip.segments?.length||1)-1);
    $("trip-summary").textContent=`${trip.carId} · ${w.start_ordinal.toLocaleString("hu-HU")}–${w.end_ordinal.toLocaleString("hu-HU")} / ${w.total_count.toLocaleString("hu-HU")} fogadott minta · ${first?reading(first.sim_time,"s",2):"—"} → ${last?reading(last.sim_time,"s",2):"—"}`;
    $("trip-window-note").textContent=`Betöltött ablak: legfeljebb ${w.page_limit} minta, fogadási sorrendben. A színes térképi út és az alábbi adatminőség erre az ablakra vonatkozik. Rögzített minta-/eseményhatár: #${trip.untilId} / #${trip.eventsUntilId??"—"}; az új adatok a frissítéskor kerülnek be.`;
    if(trip.contextOrigin)$("trip-window-note").textContent+=` Kiindulás: #${trip.contextOrigin.sampleId} forgalmi megfigyelés (${trip.contextOrigin.sampleUtc} UTC), csak az ekkor már fogadott adatok. Az „Új minták betöltése” új archívhatárt kér és kilép ebből a megfigyelésből. A teljesút-export ehhez a résznézethez nem érhető el.`;
    $("trip-refresh").textContent=trip.contextOrigin?"Új minták betöltése · határ bővítése":"Új minták betöltése";
    $("trip-coverage").textContent=`Szimulációs idő és UTC-fogadás külön órán. Mentett telemetria-periódus: ${reading(summary.sample_period_s,"s",2)}. ${!trip.samples.length?"Nincs fogadott pozícióminta; az eseményekből nem állítunk elő útvonalat.":summary.complete_received_archive===false?"Hiányos régi archívum: korábban törölt minták nem állíthatók vissza.":"Minden megőrzött minta elérhető a lapozással vagy az idő szerinti kereséssel, 20 000 minta felett is."} ${gaps?`${gaps} idő- vagy sorszámrés a betöltött ablakban; a térképi vonal ezeken megszakad.`:"A betöltött ablakban nem észleltünk idő- vagy sorszámrést."} Folyamatos módban a visszajátszás a rögzített archívhatárig lapoz; betöltéskor az utolsó fogadott minta marad. Időbélyeghibánál vagy visszalépő óránál megáll. Azonos időbélyegek fogadási sorrendben követik egymást, külön időtartamot nem kapnak. A teljes export a lekéréskori megőrzött mintákat tartalmazza. A minták között nem feltételezünk pozíciót.`;
  }
  function renderTripNavigation(){
    renderTripDecisionNavigation();
    $("trip-load").disabled=tripLoading;$("trip-refresh").disabled=tripLoading||!trip;
    $("trip-previous").disabled=tripLoading||!trip?.window?.previous_before_id;$("trip-next").disabled=tripLoading||!trip?.window?.next_after_id;$("trip-seek").disabled=tripLoading||!trip;$("trip-seek-time").disabled=tripLoading||!trip;
    for(const id of ["trip-start","trip-end","trip-slider"])$(id).disabled=tripLoading||!trip?.samples.length;$("trip-play").disabled=(tripLoading&&!trip?.continuing)||!trip?.samples.length;
  }
  function invalidateTripSelection(){
    stopTrip();tripVersion++;catalogueVersion++;inspectSelectedTrip.generation=(inspectSelectedTrip.generation||0)+1;tripLoading=false;trip=null;perceptionFrames.delete("trip");proximitySelections.delete("trip");$("trip-content").hidden=true;$("trip-status").textContent="Válaszd ki és töltsd be az utat";renderTripDecisions();renderTripNavigation();
  }
  async function loadTrip(refresh=false){
    return loadTripWindow({run:refresh&&trip?trip.runId:tripRunId(),car:refresh&&trip?trip.carId:$("trip-car").value,refresh,direction:refresh&&hasValue(trip?.samples[trip.index]?.sim_time)?"time":"start",simTime:refresh?trip?.samples[trip.index]?.sim_time:undefined});
  }
  async function loadTripWindow(options={}){
    const base=trip,run=options.run||base?.runId||tripRunId(),car=options.car||base?.carId||$("trip-car").value,direction=options.direction||"start",continuation=Boolean(options.continuation),generation=base?.playGeneration||0;
    if(!run||!car){tripError("Előbb válassz futást és mentett járművet.");return;}
    if(direction==="time"&&((typeof options.simTime!=="number"&&typeof options.simTime!=="string")||String(options.simTime).trim()===""||!hasValue(options.simTime)||Number(options.simTime)<0)){tripError("A keresett idő véges, nemnegatív szám legyen.");return;}
    if(continuation&&(!base?.playing||tripLoading||base.pendingWindow))return;
    const same=base?.runId===run&&base?.carId===car,explicitBounds=options.untilId!==undefined||options.eventsUntilId!==undefined,query=new URLSearchParams({run_id:run,car_id:car,direction,limit:"2000"});
    if(explicitBounds&&(!Number.isSafeInteger(options.untilId)||options.untilId<0||!Number.isSafeInteger(options.eventsUntilId)||options.eventsUntilId<0||options.refresh)||options.expectedReceiptId!==undefined&&(!Number.isSafeInteger(options.expectedReceiptId)||options.expectedReceiptId<=0||!explicitBounds||options.expectedReceiptId>options.untilId)){tripError("A kért fogadási határ vagy pontos rekordazonosító érvénytelen.");return;}
    if(explicitBounds){query.set("until_id",String(options.untilId));query.set("events_until_id",String(options.eventsUntilId));}
    else{if(same&&!options.refresh&&base.untilId!==null&&base.untilId!==undefined)query.set("until_id",String(base.untilId));if(same&&!options.refresh&&base.eventsUntilId!==null&&base.eventsUntilId!==undefined)query.set("events_until_id",String(base.eventsUntilId));}
    if(explicitBounds||!same||options.refresh||!base?.overview)query.set("include_overview","1");
    if(explicitBounds||!same||options.refresh||!base?.mission)query.set("include_mission","1");
    if(direction==="time")query.set("sim_time",String(options.simTime));if(["after","before"].includes(direction))query.set("cursor",String(options.cursor));
    if(!continuation)stopTrip();const version=++tripVersion;if(continuation)base.continuing=true;tripLoading=true;tripError();renderTripNavigation();$("trip-status").textContent=continuation?"Következő mintaablak betöltése · az utolsó fogadott pozíció marad":"Mentett mintaablak betöltése…";
    const valid=()=>version===tripVersion&&(!options.selectionGuard||options.selectionGuard())&&(!continuation||trip===base&&base.playing&&(base.playGeneration||0)===generation);
    try{
      const result=await getJSON(`/api/car/history/page?${query}`,30000);if(!valid())return;
      if(result.run_id!==run||result.car_id!==car)throw new Error("A futás vagy jármű azonosítója nem egyezik");
      const samples=result.telemetry||[],windowInfo=result.window;if(!Array.isArray(samples)||!windowInfo||typeof windowInfo!=="object"||samples.length>2000||["start_ordinal","end_ordinal","total_count","page_limit"].some(key=>!Number.isSafeInteger(windowInfo[key])||windowInfo[key]<0)||windowInfo.end_ordinal>windowInfo.total_count||windowInfo.page_limit>2000)throw new Error("Hiányos vagy túl nagy archív mintaablak");
      const boundedContext=explicitBounds||same&&!options.refresh&&Boolean(base.contextOrigin),requestedUntil=explicitBounds?options.untilId:base?.untilId,requestedEvents=explicitBounds?options.eventsUntilId:base?.eventsUntilId;
      if(boundedContext){
        if(!Number.isSafeInteger(result.until_id)||result.until_id<0||result.until_id>requestedUntil||!Number.isSafeInteger(result.events_until_id)||result.events_until_id<0||result.events_until_id>requestedEvents||!explicitBounds&&(result.until_id!==requestedUntil||result.events_until_id!==requestedEvents))throw new Error("A megfigyelés rögzített fogadási határa megváltozott");
        let previous=0;for(const point of samples){if(!Number.isSafeInteger(point.id)||point.id<=previous||point.id>result.until_id)throw new Error("A megfigyelés utáni vagy hibás sorrendű minta érkezett");previous=point.id;}
        if(!Array.isArray(result.events)||result.events.some(event=>!Number.isSafeInteger(event.id)||event.id<=0||event.id>result.events_until_id))throw new Error("A megfigyelés utáni esemény érkezett");
        if(result.next_events_before!=null&&(!Number.isSafeInteger(result.next_events_before)||result.next_events_before<=0||result.next_events_before>result.events_until_id)||result.summary&&["recorded_until_id","events_until_id"].some(key=>result.summary[key]!=null&&result.summary[key]!==result[key==="recorded_until_id"?"until_id":"events_until_id"]))throw new Error("A megfigyelés utáni összegzési vagy eseménylapozási határ érkezett");
        if(options.expectedReceiptId!==undefined&&(result.target_id!==options.expectedReceiptId||samples.at(-1)?.id!==options.expectedReceiptId||!samples.some(point=>point.id===options.expectedReceiptId)))throw new Error("A megfigyelés pontos fogadott járműrekordja nem érkezett meg");
      }
      if(continuation&&(result.until_id!==base.untilId||base.eventsUntilId!=null&&result.events_until_id!==base.eventsUntilId))throw new Error("A visszajátszás rögzített archívhatára megváltozott");
      const overview=result.overview||(same&&!options.refresh&&base.untilId===result.until_id?base.overview:null);
      if(overview&&(!Array.isArray(overview.points)||overview.points.length>4000||overview.until_id!==result.until_id))throw new Error("Érvénytelen teljesút-áttekintés");
      if(boundedContext&&overview?.points.some(point=>!Number.isSafeInteger(point.id)||point.id<=0||point.id>result.until_id))throw new Error("A megfigyelés utáni áttekintő minta érkezett");
      const mission=result.mission||(same&&!options.refresh&&base.untilId===result.until_id&&base.eventsUntilId===result.events_until_id?base.mission:null);
      if(mission&&(mission.schema_version!==1||mission.scope!=="frozen_received_trip_prefix"||mission.run_id!==run||mission.car_id!==car||mission.until_id!==result.until_id||mission.events_until_id!==result.events_until_id||!Array.isArray(mission.states)||mission.states.length>20||!Array.isArray(mission.wait_reasons)||mission.wait_reasons.length>20))throw new Error("Érvénytelen vagy más archívhatárhoz tartozó útösszegzés");
      if(boundedContext&&mission&&[mission.receipt?.first,mission.receipt?.last].some(point=>point&&(!Number.isSafeInteger(point.id)||point.id<=0||point.id>result.until_id)))throw new Error("A megfigyelés utáni útösszegzés érkezett");
      const map=result.map||(result.config?scenarioToMap(result.config):null),keepEvents=same&&!options.refresh&&base.untilId===result.until_id&&base.eventsUntilId===result.events_until_id;
      const apply=()=>{
        if(!valid())return false;const retainedView=same&&base?.view?{...base.view}:null,retainedFollow=Boolean(same&&base?.follow),playTime=base?.playTime;
        trip={runId:run,carId:car,samples,window:windowInfo,events:keepEvents?base.events:result.events||[],eventsBefore:keepEvents?base.eventsBefore:result.next_events_before||null,summary:result.summary||{},map,overview,mission,untilId:result.until_id,eventsUntilId:result.events_until_id,index:0,playing:continuation,playGeneration:continuation?generation:0,seek:result.seek,contextOrigin:options.contextOrigin||(!options.refresh&&same?base.contextOrigin:null)};
        const target=samples.findIndex(point=>point.id===result.target_id);trip.index=continuation?0:Math.max(0,target);$("trip-content").hidden=false;$("trip-slider").max=Math.max(0,samples.length-1);
        const exportQuery=tripQuery(run,car);for(const format of ["csv","json"]){const link=$("trip-export-"+format);link.setAttribute("aria-disabled",String(Boolean(trip.contextOrigin)));if(trip.contextOrigin){link.removeAttribute("href");link.title="A teljesút-export a lekéréskori egész archívumot tartalmazná. A kijelölt megfigyelés résznézetéhez nem érhető el.";}else{link.href=`/api/car/history/export?${exportQuery}&format=${format}`;link.title="";}}
        rebuildTripMap();if(retainedView){trip.view=retainedView;trip.follow=retainedFollow;updateTripView();}renderTripSummary();renderPerceptionCoverage();renderTripQuality();renderTripDecisions();renderTripMission();renderTripEvents();renderTripPoint(trip.index);
        if(continuation){trip.playTime=Math.max(number(playTime),number(samples[0]?.sim_time));trip.frame=performance.now();$("trip-play").textContent="Visszajátszás szüneteltetése";}
        const seek=result.seek;$("trip-seek-note").textContent=!seek?"Idő szerinti ugrás: a keresett időt megelőző legutolsó mentett minta.":seek.predecessor_found?`Keresett idő ${reading(seek.requested_sim_time,"s",2)} · megelőző mentett minta ${reading(seek.resolved_sim_time,"s",2)}. Az esemény pontos helyét nem feltételezzük.`:seek.resolution==="earliest_fallback"?`A keresett ${reading(seek.requested_sim_time,"s",2)} idő előtt nincs tárolt minta; a legkorábbi ${reading(seek.resolved_sim_time,"s",2)} mintát mutatjuk.`:"Nincs időbélyeges mentett minta.";
        if(options.event)$("trip-events-note").textContent=`Esemény ${reading(options.event.sim_time,"s",2)} · ${$("trip-seek-note").textContent}`;
        renderTripNavigation();return true;
      };
      if(continuation){const last=base.samples.at(-1),first=samples[0],problem=tripClockProblem(last,first);if(!first||Number(first.id)<=Number(last?.id))throw new Error("A következő ablak nem tartalmaz új fogadott mintát");if(problem){stopTrip(`${problem} · #${last.id} → #${first.id}; a folytatás kézzel vizsgálható.`);return result;}
        if(Number(first.sim_time)>base.playTime){base.pendingWindow={apply,first,generation};base.continuing=false;$("trip-status").textContent=timestampGap(last,first,base.summary)?"Adathiány az ablakhatáron · az utolsó fogadott pozíció marad":"A következő fogadott minta idejére várunk";}else apply();
      }else apply();
      return result;
    }catch(error){if(valid()){tripError(`Az útnapló ablaka nem tölthető be: ${error.message}`);if(continuation)stopTrip("Visszajátszás megállt · a következő ablak betöltése sikertelen");else $("trip-status").textContent="Betöltési hiba";}}
    finally{if(version===tripVersion){tripLoading=false;if(base&&trip===base)base.continuing=false;if(options.selectionGuard&&!options.selectionGuard())$("trip-status").textContent="Visszakeresés megszakadt · változott a kijelölt megfigyelés";renderTripNavigation();}}
  }
  function renderTripMission(){
    const summary=$("trip-mission-summary"),states=$("trip-mission-states"),reasons=$("trip-mission-reasons"),anchors=$("trip-mission-anchors");
    for(const host of [summary,states,reasons,anchors])empty(host);
    const mission=trip?.mission;
    if(!mission){$("trip-mission-note").textContent="Ehhez az archívumhoz nem érhető el teljesút-összegzés. A betöltött mintaablakból nem becsüljük meg a hiányzó útrészeket.";$("trip-mission-perception").textContent="Teljesútra vonatkozó érzékelőadat nincs.";return;}
    const valid=value=>typeof value==="number"&&Number.isFinite(value)&&value>=0;
    const count=value=>Number.isSafeInteger(value)&&value>=0?value.toLocaleString("hu-HU"):"—";
    const duration=value=>valid(value)?reading(value,"s",2):"—";
    const receipt=mission.receipt||{},timing=mission.timing||{},distance=mission.distance||{},perception=mission.perception||{};
    const metrics=[
      ["Rögzített keretek",count(receipt.sample_count),`${count(mission.events?.received_count)} szemantikus esemény · a rögzített határig`],
      ["Becsült besorolható idő",duration(timing.valid_estimated_duration_s),`${count(timing.valid_interval_count)} folytonos, érvényes mintaközből`],
      ["Nem besorolható pozitív idő",duration(timing.unknown_positive_duration_s),`${count(timing.excluded_interval_count)} kizárt mintaköz · nem kap állapotot`],
      ["Jelentett út",distance.available===true&&valid(distance.value_m)?reading(distance.value_m,"m",2):"Nem meghatározható",distance.available===true&&valid(distance.value_m)?"Mentett modellodometria különbsége; a mintarések mozgását nem mutatja.":"Hiányzó, érvénytelen vagy visszalépő útszámláló; nincs térképi pótlás."],
    ];
    for(const [label,value,note]of metrics){const card=html("div",{class:"mission-metric"});card.append(html("span",{class:"mission-metric-label"},label),html("strong",{class:"mission-metric-value"},value),html("small",{class:"mission-metric-note"},note));summary.append(card);}
    const first=receipt.first,last=receipt.last,clock=point=>valid(point?.sim_time)?duration(point.sim_time):"ismeretlen idő";
    $("trip-mission-note").textContent=`${trip.carId} · teljes rögzített fogadási tartomány: #${trip.untilId} minta / #${trip.eventsUntilId??"—"} esemény. Első → utolsó fogadott keret: ${clock(first)} → ${clock(last)}; időbélyeg-különbség ${duration(timing.span_s)}. Az állapot- és okarányok csak a besorolható idő becslései: a bal oldali minta jelentett állapota a következő folytonos mintáig. A vezérlési ok haladás közben is szerepelhet; nem pontos állásidő. A végső kerethez nem rendelünk további időt. A réseket és óratöréseket kihagyjuk.${receipt.complete_received_archive===false?` Hiányos megőrzés: ${count(receipt.prior_missing_samples_global)} korábbi minta globálisan nem elérhető; ez nem a kijelölt autó hiányzó mintáinak száma.`:" A megőrzött keretekből számolva; az adatok az archívhatár frissítéséig rögzítettek."}`;
    renderMissionDistribution(states,mission.states,"state",timing.valid_estimated_duration_s,mission.aggregation?.states_other);
    renderMissionDistribution(reasons,mission.wait_reasons,"reason",timing.valid_estimated_duration_s,mission.aggregation?.wait_reasons_other);
    $("trip-mission-perception").textContent=perception.available===true?`Teljes rögzített érzékelőindex: ${count(perception.fresh_frames)} friss · ${count(perception.stale_frames)} elavult · ${count(perception.unsampled_frames)} mintavétel előtti · ${count(perception.missing_frames)} érzékelőcsomag nélküli keret. ${count(perception.unique_sample_clocks)} egyedi eredeti mintaidő; legnagyobb érvényes mintakor ${duration(perception.max_age_s)}. A keretszám ismételt észleléseket is tartalmaz; az egyedi idő nem a friss minták száma.${perception.complete_received_archive===false?" Az érzékelőindex megőrzése hiányos.":""} Teljesúti közelségi minimum nincs a skalár indexben; a pontos találatok alább, a betöltött ablakból vizsgálhatók.`:"Ehhez a régi archívumhoz nincs skalár érzékelőindex. Teljesúti frissesség vagy közelségi minimum nem állapítható meg ebből az összegzésből; a betöltött keretek érzékelőnézete külön ellenőrizhető.";
    const identity=trip;
    for(const [label,point]of [["Első fogadott keret",first],["Utolsó fogadott keret",last]]){
      const button=html("button",{type:"button",class:"secondary-button mission-anchor"},`${label} · ${clock(point)} →`);
      button.disabled=!Number.isSafeInteger(point?.id)||point.id<=0;button.title=point?`Pontos fogadott rekord: #${point.id}${point.utc?` · ${point.utc} UTC`:""}. Nincs interpolált pozíció.`:"Nincs fogadott rekord.";
      button.addEventListener("click",()=>{if(trip===identity)jumpTripReceipt(point);});anchors.append(button);
    }
  }
  function renderMissionDistribution(host,entries,kind,total,other){
    const valid=value=>typeof value==="number"&&Number.isFinite(value)&&value>=0;
    const list=(Array.isArray(entries)?entries:[]).slice(0,20);
    if(other&&Number.isSafeInteger(other.sample_count)&&other.sample_count>0)list.push({...other,[kind]:"__other"});
    if(!list.length){host.append(html("p",{class:"trip-note"},"Nincs fogadott besorolás ebben az archívumban."));return;}
    for(const item of list){const raw=typeof item?.[kind]==="string"?item[kind]:"",label=raw==="__other"?"További besorolások":kind==="state"?(stateNames[raw]||raw||"Ismeretlen jelentett állapot"):(raw||"Nincs jelentett vezérlési ok"),duration=valid(item?.estimated_s)?item.estimated_s:null,share=duration!==null&&valid(total)&&total>0?Math.min(100,Math.max(0,duration/total*100)):null;
      const row=html("div",{class:"mission-distribution-row"}),heading=html("div",{class:"mission-row-heading"}),value=html("span",{class:"mission-value"},`${duration===null?"—":reading(duration,"s",2)} becsült · ${Number.isSafeInteger(item?.sample_count)&&item.sample_count>=0?item.sample_count.toLocaleString("hu-HU"):"—"} keret`);
      heading.append(html("span",{class:"mission-label"},label),value);const track=html("div",{class:"mission-track"}),fill=html("span",{class:`mission-fill ${kind==="reason"?"reason":"state"}`});fill.style.width=`${share??0}%`;track.append(fill);row.append(heading,track);row.title=share===null?"Nincs pozitív, besorolható becsült idő; a sáv nem igazol időtartamot.":`${fmt(share,1)}% a besorolható becsült időből. Jelentett besorolás; nem igazolt okozati magyarázat.`;host.append(row);
    }
  }
  async function jumpTripReceipt(point){
    const mission=trip?.mission;
    if(!trip||!mission||mission.run_id!==trip.runId||mission.car_id!==trip.carId||mission.until_id!==trip.untilId||mission.events_until_id!==trip.eventsUntilId||!Number.isSafeInteger(point?.id)||point.id<=0||point.id>trip.untilId)return;
    const direction=point.id===mission.receipt?.first?.id?"start":point.id===mission.receipt?.last?.id?"end":null;
    if(direction)await loadTripWindow({direction});
  }
  function renderTripQuality(){
    if(!trip)return;const analysis=window.YardPerceptionAnalysis.summarizeFrames(trip.samples);trip.quality=analysis;const {frames,samples,intervals,proximity}=analysis,min=proximity.minimum_clearance;
    $("trip-quality-fresh").textContent=`${frames.fresh} / ${frames.received}`;$("trip-quality-fresh-note").textContent=`${frames.stale} elavult · ${frames.fault_active} aktív hibával · ${frames.freshness_unknown} ismeretlen frissesség`;
    $("trip-quality-distinct").textContent=`${samples.distinct}`;$("trip-quality-distinct-note").textContent=`${samples.repeated} ismételt keret · ${frames.missing+frames.unsampled+frames.invalid} hiányzó / nem használható`;
    $("trip-quality-cadence").textContent=reading(intervals.mean_s,"s",3);$("trip-quality-cadence-note").textContent=intervals.count?`${reading(intervals.min_s,"s",3)} min. · ${reading(intervals.max_s,"s",3)} max. · eredeti friss mintaidők különbsége`:"Nincs két különböző friss mintaidő az ablakban.";
    $("trip-quality-close").textContent=`${proximity.close_samples} / ${proximity.fresh_distinct_samples}`;$("trip-quality-close-note").textContent=`${proximity.warning_samples} sárga sávos minta · ${proximity.no_return_only_samples} csak találat nélküli · ${proximity.invalid_channels} hibás csatorna`;
    const button=$("trip-quality-minimum");button.disabled=!min;button.textContent=min?`${reading(min.distance_m,"m",2)} · ${min.channel_label} · minta ${reading(min.sample_sim_time_s,"s",2)} →`:"Nincs érvényes felületi találat az ablakban";
    $("trip-quality-minimum-note").textContent=min?`${min.target.label||min.target.id} · ${min.fresh_when_received?"frissen fogadott mintából":"csak elavult mentett másolatból"}. Kattintás: pontos keret és csatorna kijelölése.`:"A találat hiánya nem bizonyítja a tér szabad állapotát.";
  }
  function renderPerceptionCoverage(){
    if(!trip)return;const stored=trip.samples.filter(s=>s.sensors&&typeof s.sensors==="object").length,observed=trip.samples.filter(s=>usableSensorFrame(s.sensors)).length,summary=trip.summary,coverage=hasValue(summary.perception_coverage)?Math.min(1,Math.max(0,Number(summary.perception_coverage))):trip.samples.length?stored/trip.samples.length:0;
    $("trip-perception-coverage").textContent=`Betöltött ablak: ${stored.toLocaleString("hu-HU")} / ${trip.samples.length.toLocaleString("hu-HU")} fogadott kerethez tartozik érzékelőcsomag, ${observed.toLocaleString("hu-HU")} megjeleníthető. Teljes rögzített archív csomaglefedettség: ${fmt(coverage*100,1)}%. A régi hiányzó mintáknál üres nézet jelenik meg. Az elavult csomag megtartja eredeti mintavételi helyét és idejét; nincs interpolált észlelés.`;
  }
  async function loadOlderTripEvents(){if(!trip?.eventsBefore||tripLoading)return;const identity=trip,version=tripVersion;$("trip-events-older").disabled=true;try{const data=await getJSON(`/api/car/history?${tripQuery(trip.runId,trip.carId)}&after=${number(trip.samples.at(-1)?.id)}&limit=1&until_id=${trip.untilId??0}&events_before=${trip.eventsBefore}${trip.eventsUntilId!=null?`&events_until_id=${trip.eventsUntilId}`:""}`,30000);if(trip!==identity||version!==tripVersion)return;const ids=new Set(trip.events.map(e=>e.id));trip.events.push(...(data.events||[]).filter(e=>!ids.has(e.id)));trip.eventsBefore=data.next_events_before||null;renderTripEvents();}catch(error){if(trip===identity&&version===tripVersion)tripError(`A régebbi események nem tölthetők be: ${error.message}`);}finally{if(trip===identity&&version===tripVersion)$("trip-events-older").disabled=false;}}
  function holdTripGap(message){
    $("trip-status").textContent=message;$("trip-status").className="data-badge warn";if(trip&&!trip.perceptionGap){trip.perceptionGap=true;renderTripPerception(trip.samples[trip.index],true);renderTripSensorOverlay(trip.samples[trip.index],true);}
  }
  function toggleTripPlayback(){
    if(trip?.playing||trip?.continuing||trip?.pendingWindow){stopTrip();return;}if(tripLoading||!trip?.samples.length)return;
    if(trip.index>=trip.samples.length-1&&!trip.window?.next_after_id)renderTripPoint(0);
    const problem=tripClockProblem(trip.samples[trip.index]);if(problem){stopTrip(problem);$("trip-status").textContent=problem;$("trip-status").className="data-badge warn";return;}
    trip.playing=true;trip.playGeneration=(trip.playGeneration||0)+1;trip.playTime=Number(trip.samples[trip.index].sim_time);trip.frame=performance.now();$("trip-play").textContent="Visszajátszás szüneteltetése";renderTripPoint(trip.index);
  }
  function animateTrip(now){
    if(trip?.playing&&trip.samples.length&&!trip.continuing){
      const delta=Math.max(0,Math.min(.25,(now-trip.frame)/1000));trip.frame=now;trip.playTime+=delta*number($("trip-replay-speed").value,4);
      if(trip.pendingWindow){const pending=trip.pendingWindow;if(pending.generation!==(trip.playGeneration||0)){delete trip.pendingWindow;}else if(Number(pending.first.sim_time)<=trip.playTime){pending.apply();}else if(timestampGap(trip.samples[trip.index],pending.first,trip.summary))holdTripGap("Adathiány az ablakhatáron · az utolsó fogadott pozíció marad");}
      if(trip?.playing&&!trip.pendingWindow){let index=trip.index,problem=tripClockProblem(trip.samples[index]);
        while(!problem&&index+1<trip.samples.length){const next=trip.samples[index+1];problem=tripClockProblem(trip.samples[index],next);if(problem||Number(next.sim_time)>trip.playTime)break;index++;}
        if(index!==trip.index)renderTripPoint(index);
        if(problem)stopTrip(`${problem} · a visszajátszás megállt #${trip.samples[index]?.id} mintánál.`);
        else if(index===trip.samples.length-1){if($("trip-continuous").checked&&trip.window?.next_after_id&&!tripLoading)loadTripWindow({direction:"after",cursor:trip.window.next_after_id,continuation:true});else stopTrip(trip.window?.next_after_id?"A betöltött ablak vége · a következő rész külön tölthető be":"A rögzített út visszajátszása befejeződött");}
        else{const next=trip.samples[index+1];if(timestampGap(trip.samples[index],next,trip.summary)&&trip.playTime>Number(trip.samples[index].sim_time)+number(trip.summary.sample_period_s,.1)*1.55)holdTripGap("Adathiány · az utolsó fogadott pozíció marad");}
      }
    }
    requestAnimationFrame(animateTrip);
  }
  async function inspectSelectedTrip(){
    const id=selectedCar,run=observedRun,liveVersion=carVersion;if(!id||!run)return;let generation=inspectSelectedTrip.generation=(inspectSelectedTrip.generation||0)+1;
    const valid=()=>generation===inspectSelectedTrip.generation&&id===selectedCar&&run===observedRun&&liveVersion===carVersion;
    await loadTripRuns();if(!valid())return;invalidateTripSelection();generation=inspectSelectedTrip.generation=(inspectSelectedTrip.generation||0)+1;tripRunPinned=true;$("trip-run").value=run;$("trip-search").value=id;$("trip-car").value="";
    await loadTripCars();if(!valid()||tripRunId()!==run)return;$("trip-car").value=id;if($("trip-car").value!==id){tripError("A kijelölt autóhoz ebben a futásban még nincs mentett út.");return;}
    await loadTripWindow({run,car:id,direction:"start"});if(valid())$("trip-panel").scrollIntoView({behavior:"smooth",block:"start"});
  }
  async function tripCatalogueLoop(){if(serverReachable&&!tripLoading){await loadTripRuns();if(!$("trip-car").value)await loadTripCars();}setTimeout(tripCatalogueLoop,10000);}
  function filters() {return {level:$("log-level").value,category:$("log-category").value,car_id:$("log-car").value,search:$("log-search").value.trim()};}
  function requestLogs(resetPage=true) {if(resetPage){logCursor=null;logCursorStack=[];logNextBefore=null;}logVersion++;logsDirty=true;$("log-status").textContent="Szűrés / napló betöltése…";renderLogNavigation();}
  function renderLogNavigation() {
    const live=logCursor===null;$("log-mode").className=`data-badge ${live?"good":"neutral"}`;$("log-mode").replaceChildren(html("i"),document.createTextNode(live?" Élő nézet":" Archív oldal"));
    $("log-latest").disabled=live||logsBusy;$("log-newer").disabled=!logCursorStack.length||logsBusy;$("log-older").disabled=logNextBefore===null||logNextBefore===undefined||logsBusy;
  }
  function utcTime(utc) {const d=new Date(utc);return Number.isNaN(d.getTime())?String(utc||"—"):d.toLocaleTimeString("hu-HU",{timeZone:"UTC",hour:"2-digit",minute:"2-digit",second:"2-digit"});}
  function renderLogs(result) {
    const rows=(Array.isArray(result.items)?result.items:[]).slice(0,100).sort((a,b)=>number(b.id)-number(a.id));
    registerCars(rows.map(row=>row.car_id).filter(Boolean));
    logNextBefore=result.next_before??null;const signature=JSON.stringify(rows);
    if(signature!==logLastSignature){logLastSignature=signature;const host=$("event-list");empty(host);
      if(!rows.length){const row=html("tr");row.append(html("td",{colspan:"5",class:"log-empty"},"Nincs a szűrőknek megfelelő naplóbejegyzés."));host.append(row);}
      for(const item of rows){const tr=html("tr",{class:`log-row ${item.level||"info"}`});const time=html("td",{class:"log-time"});time.append(html("time",{title:item.utc||""},timeText(item.sim_time)),html("small",{},`${utcTime(item.utc)} UTC · #${item.id}`));const level=html("td");level.append(html("span",{class:`level-badge ${item.level||"info"}`},{info:"INFO",warning:"FIGY.",error:"HIBA"}[item.level]||item.level||"INFO"));const category=html("td",{class:"log-category"},categories[item.category]||item.category||"—");const car=html("td");if(item.car_id){const button=html("button",{class:"car-link",type:"button"},item.car_id);button.addEventListener("click",()=>selectCar(item.car_id));car.append(button);}else car.textContent="—";
        const message=html("td",{class:"log-message"}),detail=html("details",{class:"log-details"});detail.open=expandedLogIds.has(String(item.id));const summary=html("summary",{},item.message||item.event_type||"Esemény");const type=html("span",{class:"event-type"},item.event_type||"—");summary.append(type);detail.append(summary);const pre=html("pre",{},JSON.stringify(item.details||{},null,2));detail.append(pre);detail.addEventListener("toggle",()=>{if(detail.open)expandedLogIds.add(String(item.id));else expandedLogIds.delete(String(item.id));});message.append(detail);tr.append(time,level,category,car,message);host.append(tr);}}
    $("log-status").textContent=`${rows.length} megjelenített sor · ${number(result.total,rows.length).toLocaleString("hu-HU")} találat`;
    $("log-page-note").textContent=`${logCursorStack.length?`${logCursorStack.length+1}. oldal · `:""}Legfeljebb 100 sor / oldal · ${logCursor===null?"élő frissítés 1 s":"az archív oldal nem gördül automatikusan"}`;
    $("log-error").hidden=true;renderLogNavigation();
  }
  async function logsLoop() {
    const run=observedRun,version=logVersion;
    if(run&&serverReachable&&!logsBusy&&(logCursor===null||logsDirty)){logsBusy=true;renderLogNavigation();const query=new URLSearchParams({limit:"100",...filters()});if(logCursor!==null)query.set("before",String(logCursor));
      try{const result=await getJSON(`/api/logs?${query}`);if(run===observedRun&&version===logVersion&&result.run_id===run){logsDirty=false;renderLogs(result);}}
      catch(error){if(run===observedRun&&version===logVersion){$("log-error").hidden=false;$("log-error").textContent=`A részletes napló jelenleg nem érhető el (${error.message}). A lekérdezés automatikusan újrapróbálkozik.`;}}
      finally{logsBusy=false;renderLogNavigation();}}
    setTimeout(logsLoop,1000);
  }

  async function receiptsLoop() {
    if(serverReachable&&pendingControls.size){
      try{const result=await getJSON('/api/logs?category=control&limit=50');
        for(const row of result.items||[]){const id=row.details?.control_id;
          if(!pendingControls.has(id)||!['control_applied','control_rejected'].includes(row.event_type))continue;
          pendingControls.delete(id);showToast(row.message||row.event_type,row.event_type==='control_rejected');}
        for(const [id,started]of pendingControls)if(performance.now()-started>10000){pendingControls.delete(id);showToast(`A parancs alkalmazásáról még nincs nyugta: ${id}. A naplóban visszakövethető.`,true);}
      }catch(error){/* The connection badge already exposes failed HTTP reads. */}
    }
    setTimeout(receiptsLoop,1000);
  }

  function render(state) {
    synchronizeRunSettings(state);
    current=state;const map=state.map||{};const key=`${state.run_id}:${map.width}:${(map.slots||[]).map(s=>`${s.id}:${s.x}:${s.y}`).join(",")}`;if(!previewMap&&key!==mapKey){mapKey=key;selectedSlot=null;buildMap(map);}fillSelect($("slot-select"),(map.slots||[]).map(s=>({value:s.id,label:`${s.id} · parkolóhely`})),"Nincs parkolóhely");fillSelect($("crossing-select"),(map.crossings||[]).map((s,i)=>({value:s.id,label:s.label||`Zebra ${i+1} · ${i===0?"kijárat":"parkolóút"}`})),"Nincs zebra");
    $("map-wait").hidden=Boolean(state.map);$("notice").hidden=true;setConnection(Boolean(state.connected),null,state.mqtt?.transport);$("run-status").textContent=state.paused?"A futás szünetel":"Szimuláció folyamatban";$("run-dot").classList.toggle("paused",Boolean(state.paused));$("sim-time").textContent=timeText(state.sim_time);$("run-seed").textContent=Number.isInteger(state.seed)?state.seed:"—";$("run-id").textContent=state.run_id||"—";$("run-id").title=state.run_id||"";$("strategy-label").textContent=state.strategy==="fixed"?"Rögzített helyszámsorrend":"Legrövidebb elérhető út";$("active-count").textContent=`${(state.cars||[]).length} / ${state.max_active||6}`;$("pause-label").textContent=state.paused?"Folytatás":"Szünet";$("pause-icon").textContent=state.paused?"▶":"Ⅱ";$("speed-label").textContent=`${state.speed||1}×`;for(const b of document.querySelectorAll("[data-speed]"))b.classList.toggle("selected",number(b.dataset.speed)===number(state.speed,1));
    const metrics=state.metrics||{};for(const [id,key]of [["buffered-count","buffered"],["offered-count","offered"],["admitted-count","admitted"]])$(id).textContent=Number.isFinite(metrics[key])?metrics[key].toLocaleString("hu-HU"):"—";$("metric-time").title=Number.isFinite(metrics.avg_yard_time_s)?`Udvaron belüli részidő: ${fmt(metrics.avg_yard_time_s)} s`:"A gyártósori készre jelentéstől a parkolásig, pufferben várakozással.";$("metric-time").textContent=number(metrics.completed)>0?`${fmt(metrics.avg_parking_time_s)} s`:"—";$("metric-distance").textContent=number(metrics.completed)>0?`${fmt(metrics.avg_distance_m)} m`:"—";$("metric-completed").textContent=number(metrics.completed).toLocaleString("hu-HU");$("pedestrian-stops").textContent=number(metrics.pedestrian_stops);$("collisions").textContent=number(metrics.collisions);$("collision-dot").classList.toggle("red",number(metrics.collisions)>0);const zones=state.zones||[],owned=zones.filter(z=>z.owner);$("zone-status").textContent=`${zones.length} forgalmi zóna · ${owned.length} foglalt`;$("zone-status").title=zones.map(z=>`${z.id}: ${z.owner||"szabad"}`).join(" · ");for(const group of $("zones-layer").children){const z=zones.find(v=>v.id===group.dataset.zone);if(!previewMap){group.classList.toggle("zone-owned",Boolean(z?.owner));group.setAttribute("aria-label",`${group.dataset.zone}: ${z?.owner||"szabad"}`);}}$("fleet-note").textContent=`Az aktív korlát ${state.max_active||6} autó, a parkoló járműveket is beleszámítva. A kezdeti parkolófoglaltság külön helyállapot.`;
    const mqtt=state.mqtt||{};$("mqtt-published").textContent=number(mqtt.published).toLocaleString("hu-HU");$("mqtt-received").textContent=number(mqtt.received).toLocaleString("hu-HU");const latency=typeof mqtt.latency_ms==="object"?mqtt.latency_ms?.mean:mqtt.latency_ms;$("mqtt-latency").textContent=Number.isFinite(Number(latency))&&latency!==null?`${fmt(latency,2)} ms`:"—";
    registerCars(state.cars||[]);if(!selectedCar&&(state.cars||[]).length)selectCar(state.cars[0].id);renderParking();renderCars();renderPedestrians();renderFleet();renderWarnings();renderCarDetails();renderSensors();fillSelect($("car-select"),(state.cars||[]).filter(c=>c.state!=="parked").map(c=>({value:c.id,label:`${c.id} · ${stateNames[c.state]||c.state}`})),"Nincs mozgó autó");renderObservability(state);if(followCar&&!previewMap){const car=(state.cars||[]).find(c=>c.id===selectedCar);if(car){view.x=number(car.x)-view.w/2;view.y=number(car.y)-view.h/2;}else followCar=false;}updateView();refreshControls();
  }

  function refreshControls() {const offline=!serverReachable||current?.connected!==true;for(const b of document.querySelectorAll("button[data-control]")){const reset=["reset-button","scenario-apply"].includes(b.id),recovery=b.id==="pause-button"?recoveryControlAllowed("pause"):b.dataset.speed==="1"?recoveryControlAllowed("speed",{value:1}):false;b.disabled=busy||(offline&&!reset&&!recovery);b.title=recovery?"Elavult kijelzett állapot mellett csak helyreállító parancs küldhető; a végrehajtást az MQTT-visszajelzés igazolja.":offline&&!reset?"Nincs friss kezelhető szimulátorállapot; a művelet jelenleg nem küldhető.":"";}
    $("pause-label").textContent=current?.connected===true?(current?.paused?"Folytatás":"Szünet"):"Szünet kérése · elavult állapot";$("pause-icon").textContent=current?.connected===true&&current?.paused?"▶":"Ⅱ";
    $("scenario-preview").disabled=previewBusy||!scenarioData;$("scenario-download").disabled=!scenarioData;$("initial-car-add").disabled=!scenarioDraft||number(scenarioDraft.initial_cars?.length)>=number(scenarioDraft.car_count,6);$("scenario-apply").disabled=busy||previewBusy||Boolean(coordinateEdit)||!scenarioData||$("scenario-apply").dataset.previewInvalid==="true";$("freeze-button").disabled=busy||offline||!$("car-select").value;}
  async function control(action,data={},successMessage=null) {
    if(busy)return null;if(!["reset","configure"].includes(action)&&(!serverReachable||current?.connected!==true)&&!recoveryControlAllowed(action,data)){showToast("Nincs friss kezelhető szimulátorállapot. Ez a művelet jelenleg nem küldhető; új futás indítható.",true);return null;}
    busy=true;refreshControls();try{const response=await fetch("/api/control",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({action,...data})});const result=await response.json();if(!response.ok||result.error)throw new Error(result.error||`HTTP ${response.status}`);if(result.control_id&&!["reset","configure"].includes(action))pendingControls.set(result.control_id,performance.now());if(successMessage||result.control_id)showToast(`${successMessage||"Parancs elküldve"}${result.control_id?` · azonosító: ${result.control_id}`:""}`);logsDirty=true;if(["reset","configure"].includes(action)){runSettingsDirty=false;pendingSettingsRun=result.run_id||null;}return result;}
    catch(error){showToast(`A művelet nem sikerült: ${error.message}`,true);if(action==="configure")scenarioError(error.message);return null;}finally{busy=false;refreshControls();}
  }
  function duration(){const value=number($("fault-duration").value,5);if(value<1||value>60){showToast("A hiba időtartama 1 és 60 másodperc közé essen.",true);return null;}return value;}
  $("pause-button").addEventListener("click",togglePauseRequest);for(const b of document.querySelectorAll("[data-speed]"))b.addEventListener("click",()=>control("speed",{value:number(b.dataset.speed)}));
  $("reset-button").addEventListener("click",()=>{const seed=number($("seed-input").value,-1);if(!Number.isInteger(seed)||seed<0||seed>2147483647){showToast("A véletlenmag 0 és 2147483647 közötti egész szám legyen.",true);return;}control("reset",{seed,strategy:$("strategy-select").value},`Új futás indult · véletlenmag: ${seed}`);});
  $("pedestrian-button").addEventListener("click",()=>control("pedestrian",{crossing_id:$("crossing-select").value},"Gyalogos hozzáadása elküldve"));$("block-button").addEventListener("click",()=>control("block_slot",{slot_id:$("slot-select").value},"Parkolóhely lezárásának váltása elküldve"));$("slot-select").addEventListener("change",()=>{selectedSlot=$("slot-select").value;renderParking();});
  $("sensor-button").addEventListener("click",()=>{const seconds=duration();if(seconds!==null)control("sensor_failure",{seconds},`Érzékelőhiba parancs elküldve · ${seconds} s`);});$("disconnect-button").addEventListener("click",()=>{const seconds=duration();if(seconds!==null)control("disconnect",{seconds},`MQTT-kiesés parancs elküldve · ${seconds} s`);});$("freeze-button").addEventListener("click",()=>{const seconds=duration(),car_id=$("car-select").value;if(seconds!==null&&car_id)control("freeze_car",{car_id,seconds},`${car_id} megállítási parancsa elküldve · ${seconds} s`);});$("show-routes").addEventListener("change",()=>{if(current)renderCars();});
  $("show-sensors").addEventListener("change",renderSensors);
  for(const prefix of ["live","trip"])for(const kind of ["svg","json"])$(`${prefix}-perception-${kind}`).addEventListener("click",event=>preparePerceptionDownload(event,prefix,kind));
  for(const prefix of ["live","trip"]){$(`${prefix}-proximity-focus`).addEventListener("click",()=>focusProximityChannel(prefix));$(`${prefix}-proximity-clear`).addEventListener("click",()=>selectProximityChannel(prefix,null));}
  $("inspect-car-select").addEventListener("change",()=>selectCar($("inspect-car-select").value));$("inspect-logs-button").addEventListener("click",()=>{if(selectedCar){$("log-car").value=selectedCar;requestLogs();$("logs-panel").scrollIntoView({behavior:window.matchMedia("(prefers-reduced-motion: reduce)").matches?"instant":"smooth",block:"start"});}});
  $("inspect-trip-button").addEventListener("click",inspectSelectedTrip);
  $("trip-run").addEventListener("change",()=>{tripRunPinned=true;catalogueCursor=null;invalidateTripSelection();$("trip-car").value="";loadTripCars();});$("trip-car").addEventListener("change",invalidateTripSelection);$("trip-search").addEventListener("input",()=>{clearTimeout(tripSearchTimer);tripSearchTimer=setTimeout(()=>loadTripCars(),300);});$("trip-more-cars").addEventListener("click",()=>loadTripCars(true));$("trip-load").addEventListener("click",()=>loadTrip());$("trip-refresh").addEventListener("click",()=>loadTrip(true));$("trip-events-older").addEventListener("click",loadOlderTripEvents);
  $("trip-previous").addEventListener("click",()=>loadTripWindow({direction:"before",cursor:trip?.window?.previous_before_id}));$("trip-next").addEventListener("click",()=>loadTripWindow({direction:"after",cursor:trip?.window?.next_after_id}));
  const seekTrip=()=>loadTripWindow({direction:"time",simTime:$("trip-seek-time").value});$("trip-seek").addEventListener("click",seekTrip);$("trip-seek-time").addEventListener("keydown",event=>{if(event.key==="Enter"){event.preventDefault();seekTrip();}});
  $("trip-quality-minimum").addEventListener("click",()=>{const minimum=trip?.quality?.proximity.minimum_clearance;if(!minimum)return;stopTrip();renderTripPoint(minimum.row_index);selectProximityChannel("trip",minimum.channel_id);$("trip-perception-title").scrollIntoView({behavior:"smooth",block:"start"});});
  $("trip-decisions-previous").addEventListener("click",()=>stepTripDecision(-1));$("trip-decisions-next").addEventListener("click",()=>stepTripDecision(1));
  $("trip-decisions-minimum").addEventListener("click",()=>jumpTripDecision(trip?.decisionWindow?.minimum_moving_reserve?.index));$("trip-decisions-strongest").addEventListener("click",()=>jumpTripDecision(trip?.decisionWindow?.strongest_braking?.index));
  $("trip-slider").addEventListener("input",()=>{stopTrip();renderTripPoint($("trip-slider").value);});$("trip-start").addEventListener("click",()=>loadTripWindow({direction:"start"}));$("trip-end").addEventListener("click",()=>loadTripWindow({direction:"end"}));$("trip-play").addEventListener("click",toggleTripPlayback);
  $("trip-show-overview").addEventListener("change",renderTripOverview);$("trip-continuous").addEventListener("change",()=>{if(!$("trip-continuous").checked&&(trip?.continuing||trip?.pendingWindow))stopTrip("Folyamatos lejátszás kikapcsolva · mentett minta");});
  installTripMapControls();
  new ResizeObserver(()=>{updateView();resizeTripMap();}).observe($("yard-map"));new ResizeObserver(resizeTripMap).observe($("trip-map"));requestAnimationFrame(animateTrip);setTimeout(tripCatalogueLoop,500);
  for(const id of ["log-level","log-category","log-car"])$(id).addEventListener("change",()=>requestLogs());$("log-search").addEventListener("input",()=>requestLogs());$("log-clear").addEventListener("click",()=>{for(const id of ["log-level","log-category","log-car","log-search"])$(id).value="";requestLogs();});
  $("log-older").addEventListener("click",()=>{if(logNextBefore!==null){logCursorStack.push(logCursor);logCursor=logNextBefore;requestLogs(false);}});$("log-newer").addEventListener("click",()=>{if(logCursorStack.length){logCursor=logCursorStack.pop();requestLogs(false);}});$("log-latest").addEventListener("click",()=>requestLogs());
  $("editor-toggle").addEventListener("click",()=>{const open=$("editor-body").hidden;$("editor-body").hidden=!open;$("editor-toggle").setAttribute("aria-expanded",String(open));$("editor-toggle").replaceChildren(document.createTextNode(open?"Beállítások bezárása ":"Beállítások megnyitása "),html("span",{"aria-hidden":"true"},open?"⌃":"⌄"));});
  for(const id of ["scenario-preset",...Object.values(scenarioFieldIds),"scenario-exit","scenario-area","scenario-limit"]){$(id).addEventListener("change",()=>{if(!scenarioDraft)return;const resetLayout=["scenario-preset","scenario-area","scenario-parking"].includes(id),resetCars=["scenario-preset","scenario-exit"].includes(id);readScenarioFields();if(resetLayout)delete scenarioDraft.slots;if(resetCars)delete scenarioDraft.initial_cars;if(id==="scenario-preset"){delete scenarioDraft.production_exit_id;delete scenarioDraft.parking_area_id;}markScenarioDirty();scenarioError();schedulePreview();});}
  $("scenario-preview").addEventListener("click",()=>{clearTimeout(previewTimer);markScenarioDirty();previewScenario();});$("scenario-apply").addEventListener("click",applyScenario);$("scenario-discard").addEventListener("click",discardScenario);$("placement-mode").addEventListener("change",async()=>{clearCoordinateEdit();if($("placement-mode").value!=="none"&&!previewMap){markScenarioDirty();await previewScenario();}fillPlacementItems();});$("placement-item").addEventListener("change",()=>{clearCoordinateEdit();fillPlacementItems($("placement-item").value);});$("placement-save").addEventListener("click",()=>savePlacement());$("initial-car-add").addEventListener("click",addInitialCar);$("initial-car-remove").addEventListener("click",()=>{if($("placement-mode").value!=="car"||!placementPosition())return;clearCoordinateEdit();scenarioDraft.initial_cars.splice(Number($("placement-item").value),1);scenarioDraft.initial_car_count=scenarioDraft.initial_cars.length;$("scenario-initial-cars").value=scenarioDraft.initial_car_count;markScenarioDirty();fillPlacementItems();schedulePreview();});
  $("scenario-download").addEventListener("click",downloadScenario);$("scenario-import").addEventListener("change",importScenario);
  $("seed-input").addEventListener("change",()=>{if(previewMap){markScenarioDirty();schedulePreview();}});
  for(const id of ["seed-input","strategy-select"])for(const eventName of ["input","change"])$(id).addEventListener(eventName,()=>{runSettingsDirty=true;});
  for(const id of ["placement-x","placement-y"])$(id).addEventListener("input",editCoordinates);
  $("map-zoom-in").addEventListener("click",()=>zoomMap(.7));$("map-zoom-out").addEventListener("click",()=>zoomMap(1/.7));$("map-fit").addEventListener("click",fitMap);$("map-focus-car").addEventListener("click",()=>{if(followCar){followCar=false;updateView();}else focusCar();});
  $("yard-map").addEventListener("wheel",event=>{if(!event.ctrlKey)return;event.preventDefault();zoomMap(event.deltaY>0?1.15:1/1.15,mapPoint(event));},{passive:false});
  $("yard-map").addEventListener("pointerdown",event=>{if(event.button!==0)return;const point=mapPoint(event);if(!point)return;drag={id:event.pointerId,start:{x:event.clientX,y:event.clientY},point,view:{...view},matrix:$("yard-map").getScreenCTM().inverse()};movedPointer=false;});
  $("yard-map").addEventListener("pointermove",event=>{const point=mapPoint(event);if(point)$("map-coordinates").textContent=`X ${fmt(point.x)} · Y ${fmt(point.y)} m`;if(!drag||drag.id!==event.pointerId)return;const distance=Math.hypot(event.clientX-drag.start.x,event.clientY-drag.start.y);if(distance<4&&!movedPointer)return;movedPointer=true;followCar=false;$("yard-map").setPointerCapture(event.pointerId);const screen=$("yard-map").createSVGPoint();screen.x=event.clientX;screen.y=event.clientY;const transformed=screen.matrixTransform(drag.matrix);view={...drag.view,x:drag.view.x+drag.point.x-transformed.x,y:drag.view.y+drag.point.y-transformed.y};updateView();});
  for(const eventName of ["pointerup","pointercancel"]){$("yard-map").addEventListener(eventName,event=>{if(drag?.id!==event.pointerId)return;if($("yard-map").hasPointerCapture(event.pointerId))$("yard-map").releasePointerCapture(event.pointerId);drag=null;});}
  $("yard-map").addEventListener("pointerleave",()=>{if(!drag)$("map-coordinates").textContent="Húzás: térkép mozgatása · Ctrl + görgő: nagyítás";});
  $("yard-map").addEventListener("click",event=>{if(movedPointer||!previewMap||$("placement-mode").value==="none"||event.target.closest('[role="button"]'))return;const point=mapPoint(event);if(point)savePlacement(point.x,point.y);});
  async function poll(){try{const state=await getJSON("/api/state");serverReachable=true;lastFetchOk=performance.now();if(state.run_id&&state.run_id!==observedRun)resetRunCaches(state.run_id);if(state&&state.map)render(state);else{current=state;refreshControls();renderTransportCapacity(state);$("run-status").textContent="Várakozás az MQTT-adatútra…";if(typeof state.connected==="boolean")setConnection(state.connected,null,state.mqtt?.transport);}renderOperationsSafety();}catch(error){serverReachable=false;renderOperationsSafety();refreshControls();renderCarDetails();renderSensors();if(performance.now()-lastFetchOk>1500){markTransportCapacityOffline();setConnection(false,"Műszerfal kapcsolódik");$("notice").hidden=false;$("notice").textContent="A helyi szerver jelenleg nem elérhető. A műszerfal automatikusan újracsatlakozik; a kijelzett értékek az utolsó fogadott adatok.";}}finally{setTimeout(poll,250);}}
  let lastFrame=performance.now();function animate(now){const elapsed=Math.min((now-lastFrame)/1000,.1);lastFrame=now;const smooth=window.matchMedia("(prefers-reduced-motion: reduce)").matches?1:1-Math.exp(-elapsed*15);for(const e of carNodes.values()){e.x+=(e.targetX-e.x)*smooth;e.y+=(e.targetY-e.y)*smooth;const d=((e.targetHeading-e.heading+540)%360)-180;e.heading+=d*smooth;e.g.setAttribute("transform",`translate(${e.x.toFixed(3)} ${e.y.toFixed(3)})`);e.body.setAttribute("transform",`rotate(${e.heading.toFixed(2)})`);}requestAnimationFrame(animate);}requestAnimationFrame(animate);refreshControls();poll();carLoop();logsLoop();receiptsLoop();loadScenario();installTransportArchive();installOperations();
})();
