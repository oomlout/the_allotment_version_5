"""Run the same fixed-sample preparation check as Studio. Never submit or approve."""
import argparse
from pathlib import Path
import sys
import time
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from allotment.preparation import PreparationManager, folder_for, preparation_progress, TERMINAL
from allotment.storage import Store

if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('platform');parser.add_argument('--namespace',default='the_allotment')
    parser.add_argument('--user',default='oomlout_main');parser.add_argument('--inspect',action='store_true')
    args=parser.parse_args();store=Store(Path(__file__).resolve().parents[1]);manager=PreparationManager(store)
    run_id=manager.start(args.namespace,args.user,args.platform,mode='inspect' if args.inspect else 'prepare')
    folder=folder_for(store,args.namespace,args.user,run_id);print(run_id,flush=True);last=None
    try:
        while True:
            progress=preparation_progress(folder)
            state=(progress.get('status'),progress.get('operation'),progress.get('error'))
            if state!=last:print(state,flush=True);last=state
            if progress['status'] in TERMINAL:break
            time.sleep(.5)
    except KeyboardInterrupt:
        manager.stop(args.namespace,args.user,run_id);raise
    print(folder,flush=True)
    sys.exit(0 if progress['status'] in {'prepared','inspected'} else 1)
