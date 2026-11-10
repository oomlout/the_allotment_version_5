"""Apply a reviewed tag plan and generate missing image crops. No Git or worker actions."""
import argparse
import json
from pathlib import Path
import sys
import uuid
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from allotment.storage import Store,inside,read_yaml,write_yaml,now
from allotment.tags import save_tag
from allotment.images import generate_variants,digest


def enrich(store,namespace,user,plan,apply=False):
    posts={p['id']:p for p in store.posts(namespace,user)}
    for post_id,names in plan['posts'].items():
        if post_id not in posts or not posts[post_id].get('legacy'):raise ValueError(f'Not an imported post: {post_id}')
        if any(name not in plan['tags'] for name in names):raise ValueError('Every planned tag needs a group.')
    if not apply:return {'posts':len(plan['posts']),'tags':plan['tags'],'changes':plan['posts'],'applied':False}
    backup=store.local/'library-backups'/('legacy-'+uuid.uuid4().hex)
    report={'at':now(),'backup':str(backup),'applied':True,'posts':[]}
    with store.garden_lock(namespace,user):
        repo=store.repo(namespace,user)
        originals={p: digest(p) for post_id in plan['posts'] for p in [inside(repo,posts[post_id]['path']+'/'+a['file']) for a in posts[post_id]['attachments']]}
        queue={p:digest(p) for p in (repo/'.allotment/queue').glob('*.yaml')}
        for path in [repo/'tags.yaml',*[repo/posts[key]['path']/'post.yaml' for key in plan['posts']]]:
            if not path.exists():continue
            target=inside(backup,path.relative_to(store.root));target.parent.mkdir(parents=True,exist_ok=True);target.write_bytes(path.read_bytes())
        catalogue={name:save_tag(store,namespace,user,name,group)['id'] for name,group in plan['tags'].items()}
        for post_id,names in plan['posts'].items():
            post=posts[post_id];folder=inside(repo,post['path'])
            post['tags']=list(dict.fromkeys([*post.get('tags',[]),*[catalogue[name] for name in names]]))
            post.pop('path',None)
            write_yaml(folder/'post.yaml',post)
            errors=[]
            for index,attachment in enumerate(post['attachments']):
                if attachment['type']!='image':continue
                try:post=generate_variants(folder,post,index)
                except ValueError as error:errors.append(str(error))
            report['posts'].append({'id':post_id,'tags':names,'variants':sum(len(a.get('variants',{})) for a in post['attachments']),'warnings':errors})
        if any(digest(path)!=value for path,value in originals.items()):raise AssertionError('An original was modified.')
        if {p:digest(p) for p in (repo/'.allotment/queue').glob('*.yaml')}!=queue:raise AssertionError('The publishing queue changed.')
        report['originals_unchanged']=True;report['queue_unchanged']=True
        write_yaml(backup/'report.yaml',report)
    return report


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('plan',type=Path)
    parser.add_argument('--root',type=Path,default=Path(__file__).resolve().parents[1]);parser.add_argument('--namespace',default='the_allotment');parser.add_argument('--user',default='oomlout_main');parser.add_argument('--apply',action='store_true')
    args=parser.parse_args();print(json.dumps(enrich(Store(args.root),args.namespace,args.user,read_yaml(args.plan),args.apply),indent=2))
