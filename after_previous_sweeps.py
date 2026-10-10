"""Wait until every requested preceding experiment package has ended."""
import argparse
from collections import Counter
from datetime import datetime,timezone
import fcntl,json,subprocess,time
from pathlib import Path
ROOT=Path(__file__).resolve().parent
TERMINAL={'completed','failed','interrupted'}
def inspect(package):
    manifest=json.loads((package/'manifest.json').read_text())
    jobs=manifest['jobs']
    if len(jobs)!=manifest['job_count'] or len({j['id'] for j in jobs})!=len(jobs):
        raise RuntimeError('Invalid prerequisite manifest')
    states=[]
    for job in jobs:
        p=package/'results'/job['id']/'state.json'
        states.append(json.loads(p.read_text()) if p.exists() else {'status':'queued'})
    live=[]
    for s in states:
        for k in ['pid','worker_pid']:
            pid=s.get(k)
            if not isinstance(pid,int):continue
            try:
                cmd=Path(f'/proc/{pid}/cmdline').read_bytes().replace(b'\0',b' ').decode(errors='replace')
                if str(package)+'/' in cmd and ('simulator' in cmd or 'remote_campaign_runner.py' in cmd):live.append(pid)
            except OSError:pass
    return {'package':str(package),'counts':dict(Counter(s.get('status','queued') for s in states)),
            'live_pids':sorted(set(live)),'ready':all(s.get('status') in TERMINAL for s in states) and not live}
def main():
    p=argparse.ArgumentParser();p.add_argument('--after',nargs='+',type=Path,required=True);p.add_argument('--workers',type=int,default=17);p.add_argument('--check-only',action='store_true');args=p.parse_args()
    packages=[x.resolve() for x in args.after]
    if args.check_only:
        print(json.dumps([inspect(x) for x in packages],indent=2));return
    with (ROOT/'after-previous.lock').open('a') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        while True:
            states=[inspect(x) for x in packages]
            s={'checked_at':datetime.now(timezone.utc).isoformat(),'prerequisites':states,'ready':all(x['ready'] for x in states)}
            tmp=ROOT/'after-previous-status.json.tmp';tmp.write_text(json.dumps(s,indent=2)+'\n');tmp.replace(ROOT/'after-previous-status.json')
            print(json.dumps(s),flush=True)
            if s['ready']:
                with (ROOT/'results'/'supervisor.log').open('a') as output:
                    subprocess.run(['python3',str(ROOT/'remote_campaign_runner.py'),'run','--groups','PTW','--workers',str(args.workers)],cwd=ROOT,stdout=output,stderr=subprocess.STDOUT,check=True)
                return
            time.sleep(30)
if __name__=='__main__':main()
