"""Publication figures from the frozen, independently validated course campaign."""
from pathlib import Path
from datetime import datetime, timezone
import argparse, hashlib, json
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt

ROOT=Path(__file__).resolve().parents[1]
BLUE='#27668c';GOLD='#a87524';INK='#243744'
def sha(path):return hashlib.sha256(path.read_bytes()).hexdigest()
def main():
    parser=argparse.ArgumentParser();parser.add_argument('--input',type=Path,default=ROOT/'results/course-comparison-v18');parser.add_argument('--output',type=Path,default=ROOT/'docs/course-report/figures');args=parser.parse_args()
    summary=json.loads((args.input/'summary.json').read_text(encoding='utf-8'));validation=json.loads((args.input/'validation.json').read_text(encoding='utf-8'))
    assert summary['status']==validation['status']=='PASS' and not summary['pilot']
    assert summary['hashes_unchanged']
    for name,expected in summary['source_and_config_sha256_before'].items():assert sha(ROOT/name)==expected,name
    pairs=json.loads((args.input/'pairs.json').read_text(encoding='utf-8'));assert len(pairs)==60
    plt.rcParams.update({'font.family':'DejaVu Sans','font.size':10,'axes.labelcolor':INK,'text.color':INK,'axes.edgecolor':'#a9b6bd','axes.spines.top':False,'axes.spines.right':False,'svg.hashsalt':'course-v18'})
    args.output.mkdir(parents=True,exist_ok=True)
    meta=dict(Software='Matplotlib course figure renderer',CreationDate=None,Date=None)
    fig,axes=plt.subplots(2,1,figsize=(5.87,4.6),layout='constrained')
    for ax,key,label in zip(axes,['parking_time_s','distance_m'],['Időkülönbség [s/autó]','Úthosszkülönbség [m/autó]']):
        for y,s in enumerate(summary['scenarios']):
            r=s[key];mean=r['mean_difference'];lo,hi=r['ci95'];color=[BLUE,GOLD][y]
            ax.errorbar(mean,y,xerr=[[mean-lo],[hi-mean]],fmt=['o','s'][y],color=color,markersize=7,capsize=5,elinewidth=1.7)
            ax.annotate(f'{mean:.3f}  [{lo:.3f}; {hi:.3f}]',(mean,y),xytext=(0,11),textcoords='offset points',ha='center',fontsize=9)
        ax.set_yticks([0,1],['Referencia 9 s','Nagyobb terhelés 3 s']);ax.set_ylim(-.45,1.55);ax.axvline(0,color='#667782',lw=1,ls='--');ax.set_xlabel(label);ax.grid(axis='x',color='#e7ecef')
        minimum=min(s[key]['ci95'][0] for s in summary['scenarios']);ax.set_xlim(minimum*1.2,abs(minimum)*.06)
    fig.suptitle('Nearest − fixed különbség és 95% bootstrap-intervallum',fontsize=10.5)
    fig.savefig(args.output/'ci_comparison.png',dpi=300);fig.savefig(args.output/'ci_comparison.svg',metadata={'Date':None});plt.close(fig)
    fig,axes=plt.subplots(2,1,figsize=(5.87,4.3),layout='constrained',sharex=True)
    for i,(scenario,ax) in enumerate(zip(['reference_9s','high_load_3s'],axes)):
        selected=sorted([p for p in pairs if p['scenario']==scenario],key=lambda p:p['seed']);assert len(selected)==30
        x=[p['seed'] for p in selected];y=[p['paired_parking_time_s_difference'] for p in selected]
        ax.scatter(x,y,s=26,marker=['o','s'][i],color=[BLUE,GOLD][i],zorder=3)
        ax.axhline(0,color='#667782',lw=1,ls='--');ax.grid(axis='y',color='#e7ecef');ax.set_ylim(min(y)-1,1)
        ax.set_ylabel('Időkülönbség [s/autó]');ax.set_title(['Referencia 9 s','Nagyobb terhelés 3 s'][i],loc='left',fontsize=10.5)
    axes[-1].set_xlabel('Véletlenmag');axes[-1].set_xticks([1001,1005,1010,1015,1020,1025,1030]);fig.savefig(args.output/'pair_differences.png',dpi=300);fig.savefig(args.output/'pair_differences.svg',metadata={'Date':None});plt.close(fig)
    artifacts={p.relative_to(ROOT).as_posix():sha(p) for p in args.output.glob('*') if p.stem in {'ci_comparison','pair_differences'}}
    report={'status':'PASS','created_at_utc':datetime.now(timezone.utc).isoformat(),'source_sha256':{Path(__file__).relative_to(ROOT).as_posix():sha(Path(__file__)),**{p.relative_to(ROOT).as_posix():sha(p) for p in [args.input/'summary.json',args.input/'validation.json',args.input/'pairs.json']}},'artifact_sha256':artifacts,'scope':'Each scenario has 30 independent seed pairs; shared seed identities across scenarios do not represent independent cross-scenario samples. CI bars are paired-effect bootstrap intervals, not individual car dispersion. Zero reference is visible; per-pair dots are not additional independent car samples.'}
    (args.output/'charts-manifest.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8');print(json.dumps({'status':'PASS','figures':len(artifacts)}))
if __name__=='__main__':main()
