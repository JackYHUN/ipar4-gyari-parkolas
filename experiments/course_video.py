"""Render an annotated replay of received real MQTT snapshots, without interpolation.

This is a graphical reconstruction of saved simulator messages, not a browser
screen recording or a physical camera recording. Host receipt time is retained;
the last received snapshot is held and explicitly marked during reception gaps.
Requires Pillow, numpy, imageio and imageio-ffmpeg as authoring tools only.
"""
from __future__ import annotations
import argparse, bisect, hashlib, json, math
from datetime import datetime, timezone
from pathlib import Path
import imageio.v2 as imageio
import numpy as np
from PIL import Image, ImageDraw, ImageFont

ROOT=Path(__file__).resolve().parents[1]
NAVY='#152b46'; BLUE='#1c6595'; TEAL='#287d76'; GOLD='#b77716'; INK='#243448'; GRAY='#e6ebef'
FONT=Path('C:/Windows/Fonts/arial.ttf')
BOLD=Path('C:/Windows/Fonts/arialbd.ttf')
def font(size,bold=False): return ImageFont.truetype(str(BOLD if bold else FONT),size)
def sha(p): return hashlib.sha256(p.read_bytes()).hexdigest()
def txt(d,xy,s,size=22,color=INK,bold=False): d.text(xy,str(s),font=font(size,bold),fill=color)
def lines(d,xy,s,width=32,size=20,color=INK):
    words=str(s).split(); out=[]; line=''
    for w in words:
        if len(line)+len(w)>width: out.append(line); line=''
        line=(line+' '+w).strip()
    if line: out.append(line)
    for i,t in enumerate(out[:5]): txt(d,(xy[0],xy[1]+i*(size+6)),t,size,color)
    return len(out[:5])*(size+6)
def readframes(p):
    frames=[json.loads(line) for line in p.read_text(encoding='utf-8').splitlines() if line.strip()]
    assert frames and all(f['topic']=='snapshot' or f['topic'].endswith('/snapshot') for f in frames)
    assert all(b['received_ns']>=a['received_ns'] for a,b in zip(frames,frames[1:]))
    assert len({f['snapshot']['run_id'] for f in frames})==1
    return frames
def title_image(title,sub):
    im=Image.new('RGB',(1280,720),'white');d=ImageDraw.Draw(im)
    d.rectangle((0,0,1280,128),fill=NAVY)
    txt(d,(65,42),'IPAR 4.0   MÉRNÖKI BEMUTATÓ',30,'white',True)
    lines(d,(65,212),title,58,36)
    lines(d,(65,344),sub,90,25)
    txt(d,(65,610),'6 autó  •  16 parkolóhely  •  2 zebra  •  MQTT 3.1.1',24,BLUE,True)
    txt(d,(65,660),'Szimulált fizikai folyamat és tényleges helyi TCP/MQTT adatút',20)
    return np.asarray(im)
STATE={'waiting':'Engedélyre vár','driving':'Halad','waiting_zone':'Zónára vár','pedestrian_stop':'Fékez / gyalogosra vár',
       'sensor_stop':'Érzékelőhiba','communication_stop':'Kapcsolati hiba','parking':'Beáll','parked':'Beparkolt','frozen':'Kezelői megállás'}
def render(frame,title,elapsed,age):
    s=frame['snapshot'];im=Image.new('RGB',(1280,720),'#f8fafb');d=ImageDraw.Draw(im)
    d.rectangle((0,0,1280,103),fill=NAVY)
    txt(d,(28,19),title,30,'white',True)
    txt(d,(28,61),'Fogadott MQTT-adatok visszajátszása  •  grafikus rekonstrukció',20,'#d2e4ed')
    d.rectangle((28,125,915,672),fill='white',outline='#d4dce3',width=2)
    scale=min(840/140,496/90);ox=50;oy=144
    def pos(x,y):return (ox+float(x)*scale,oy+float(y)*scale)
    m=s['map']
    for b in m.get('buildings',[]):
        x,y=pos(b['x'],b['y']);d.rectangle((x,y,x+b['w']*scale,y+b['h']*scale),fill='#e6edf1',outline='#bdcbd6',width=2)
        txt(d,(x+10,y+18),b.get('label','Épület'),19)
    for road in m.get('roads',[]):
        points=[pos(*p) for p in road];d.line(points,fill='#cbd4dc',width=int(8*scale),joint='curve')
        d.line(points,fill='#8496a5',width=2)
    for z in m.get('zones',[]):
        x,y=pos(z['x'],z['y']);w=z['w']*scale;h=z['h']*scale
        d.rectangle((x-w/2,y-h/2,x+w/2,y+h/2),outline=GOLD,width=3)
    for c in m.get('crossings',[]):
        x,y=pos(c['x'],c['y'])
        for k in range(-3,4):
            if c['axis']=='vertical':d.rectangle((x-17+k*5,y-20,x-14+k*5,y+20),fill='white')
            else:d.rectangle((x-20,y-17+k*5,x+20,y-14+k*5),fill='white')
        txt(d,(x-29,y+25),c['id'].replace('ZEBRA','Z'),17)
    overlay=frame.get('central_overlay') or {};central={p['id']:p for p in overlay.get('slots',[])}
    states={p['id']:p for p in s.get('parking',[])}
    colors={'free':'#d8e9e6','reserved':'#ffe5b7','occupied':'#acbcc9','blocked':'#d8b2b2','unknown':'#f0f0f0'}
    for p in m.get('slots',[]):
        x,y=pos(p['x'],p['y']);physical=states.get(p['id'],{}).get('state','unknown');cp=central.get(p['id'],{})
        state='occupied' if physical=='occupied' else cp.get('state',physical)
        d.rounded_rectangle((x-17,y-25,x+17,y+25),radius=4,fill=colors.get(state,colors['unknown']),outline='#6a8093',width=2)
        txt(d,(x-17,y-49),p['id'],16)
    for p in s.get('pedestrians',[]):
        x,y=pos(p['x'],p['y']);d.ellipse((x-6,y-6,x+6,y+6),fill='#222a33');d.line((x,y+5,x,y+15),fill='#222a33',width=3)
    cars=s.get('cars',[])
    for car in cars:
        x,y=pos(car['x'],car['y']);heading=math.radians(car.get('heading',0));L=4.4*scale/2;W=1.8*scale/2
        poly=[(x+a*math.cos(heading)-b*math.sin(heading),y+a*math.sin(heading)+b*math.cos(heading)) for a,b in [(L,W),(L,-W),(-L,-W),(-L,W)]]
        state=car.get('state');color=GOLD if state in {'sensor_stop','communication_stop','pedestrian_stop'} else TEAL if state=='parked' else BLUE
        d.polygon(poly,fill=color,outline='#102739')
        txt(d,(x-17,y-23),car['id'].replace('CAR','C'),16,color,True)
    for i,(state,label) in enumerate([('free','Szabad'),('reserved','Lefoglalt'),('occupied','Fizikailag foglalt'),('unknown','Ismeretlen')]):
        lx=65+i*210;d.rectangle((lx,641,lx+16,657),fill=colors[state],outline='#6a8093')
        txt(d,(lx+26,638),label,17)
    txt(d,(945,128),f"Szimuláció  {s['sim_time']:.2f} s",25,INK,True)
    txt(d,(945,167),f"Fogadási idő  {elapsed:.2f} s",20)
    txt(d,(945,199),f"Snapshot #{frame['receipt_id']}",19,BLUE)
    if age>0.65:
        txt(d,(945,235),f'Nincs új snapshot: {age:.2f} s',20,GOLD)
        txt(d,(945,261),'Utolsó fogadott állapot.',20,GOLD)
    else: txt(d,(945,235),'Ténylegesen fogadott állapot',18,TEAL)
    metrics=s.get('metrics',{});txt(d,(945,303),f"Beparkolt: {metrics.get('completed',0)} / 6",22,INK,True)
    txt(d,(945,339),f"Ütközésjelzés: {metrics.get('collisions',0)}",20)
    for i,car in enumerate(cars[:6]):
        y=388+i*39;txt(d,(945,y),f"{car['id']}  {car.get('speed',0):.1f} m/s",19,INK,True)
        txt(d,(945,y+19),STATE.get(car.get('state'),car.get('state','Ismeretlen')),17,GOLD if 'stop' in car.get('state','') else BLUE)
    central_age=(frame['received_ns']-frame['central_overlay_received_ns'])/1e9+age if frame.get('central_overlay_received_ns') else None
    txt(d,(945,645),'Külön fogadott központi adatág',15)
    txt(d,(945,665),'Minta kora: ismeretlen' if central_age is None else f'Minta kora: {central_age:.2f} s',15)
    txt(d,(31,691),f"Futás: {s['run_id']}   •   Virtuális érzékelők; idealizált útvonalkövetés; fizikai kamerafelvétel nincs",17)
    return np.asarray(im)
def main():
    parser=argparse.ArgumentParser();parser.add_argument('--campaign',type=Path,required=True);parser.add_argument('--output',type=Path,required=True);args=parser.parse_args()
    args.campaign=args.campaign.resolve();args.output=args.output.resolve()
    args.output.parent.mkdir(parents=True,exist_ok=True)
    summary=json.loads((args.campaign/'summary.json').read_text(encoding='utf-8'));assert summary['status']=='PASS'
    core_summary_path=next(args.campaign/r['path'] for r in summary['reports'] if 'core' in json.loads((args.campaign/r['path']).read_text(encoding='utf-8')))
    core=core_summary_path.parent/'core/full-visual-frames.jsonl'
    sources=[(args.campaign/'nominal/full-visual-frames.jsonl','1  Normál parkolás'),(core,'2  Zebra, érzékelőhiba és kapcsolatvesztés'),(core.parent.parent/'full-parking/full-visual-frames.jsonl','3  Megtelt parkoló')]
    fps=12;clips=[];video_t=0;inputs={};preview_saved=False
    with imageio.get_writer(str(args.output),fps=fps,codec='libx264',quality=8,macro_block_size=16,ffmpeg_params=['-movflags','+faststart']) as writer:
        intro=title_image('Gyártósortól a parkolóhelyig','A bemutató a valódi MQTT-próba mentett állapotképeit jeleníti meg. A pozíciók között nincs interpoláció. A kimaradások külön jelzést kapnak.');
        for _ in range(fps*5):writer.append_data(intro)
        video_t+=5
        for p,title in sources:
            frames=readframes(p);inputs[str(p.relative_to(ROOT))]=sha(p);base=frames[0]['received_ns'];times=[(f['received_ns']-base)/1e9 for f in frames];duration=times[-1]
            if 'Megtel' in title:duration=min(duration,6)
            plate=title_image(title,'Mentett fogadási sorrend; a szimulációs idő és a gazdagép fogadási ideje külön szerepel.');
            for _ in range(fps*3):writer.append_data(plate)
            video_t+=3;start=video_t
            for tick in range(math.ceil(duration*fps)+1):
                elapsed=min(tick/fps,duration);index=bisect.bisect_right(times,elapsed)-1;frame=frames[max(0,index)];im=render(frame,title,elapsed,elapsed-times[max(0,index)])
                writer.append_data(im)
                if not preview_saved and 'Zebra' in title and any(c.get('state')=='pedestrian_stop' for c in frame['snapshot']['cars']):
                    Image.fromarray(im).save(args.output.with_suffix('.png'));preview_saved=True
            video_t+= (math.ceil(duration*fps)+1)/fps
            clips.append({'title':title,'source':str(p.relative_to(ROOT)),'snapshot_count':len(frames),'run_id':frames[0]['snapshot']['run_id'],'video_start_s':start,'video_end_s':video_t,'host_receipt_duration_s':duration})
    if not preview_saved:Image.fromarray(render(frames[-1],title,times[-1],0)).save(args.output.with_suffix('.png'))
    reader=imageio.get_reader(str(args.output));meta=reader.get_meta_data();first=reader.get_data(0);last=reader.get_data(reader.count_frames()-1);assert first.shape[:2]==last.shape[:2]==(720,1280)
    report={'status':'PASS','created_at_utc':datetime.now(timezone.utc).isoformat(),'video':str(args.output),'video_sha256':sha(args.output),'duration_s':meta.get('duration'),'fps':meta.get('fps'),'size':meta.get('size'),'clips':clips,
      'scope':'Graphical replay of actual received MQTT simulator snapshots; host receipt chronology; last observation held with explicit missing-snapshot indication; no interpolation, physical camera or browser screen-recording claim.',
      'source_sha256':{str(Path(__file__).relative_to(ROOT)):sha(Path(__file__)),**inputs},'preview_sha256':sha(args.output.with_suffix('.png'))}
    args.output.with_suffix('.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8');print(json.dumps(report,ensure_ascii=False))
if __name__=='__main__':main()
