"""Normalise tag display names, preserving IDs and backing up each catalogue."""
from pathlib import Path
import sys,uuid
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from allotment.storage import Store,read_yaml,write_yaml
from allotment.tags import tag_name,merge_tags


def normalize(store,namespace,user):
    with store.garden_lock(namespace,user):
        path=store.repo(namespace,user)/'tags.yaml'
        if not path.exists():return 0
        data=read_yaml(path)
        if all(t['name']==tag_name(t['name']) for t in data['tags']):return 0
        backup=store.local/'library-backups'/('tag-names-'+uuid.uuid4().hex)/path.relative_to(store.root)
        backup.parent.mkdir(parents=True,exist_ok=True);backup.write_bytes(path.read_bytes())
        seen={}
        for tag in list(data['tags']):
            name=tag_name(tag['name'])
            if name in seen:merge_tags(store,namespace,user,tag['id'],seen[name])
            else:seen[name]=tag['id']
        data=read_yaml(path)
        for tag in data['tags']:tag['name']=tag_name(tag['name'])
        data['tags'].sort(key=lambda tag:(tag['group'].casefold(),tag['name']))
        write_yaml(path,data)
        return len(data['tags'])


if __name__=='__main__':
    store=Store(Path(__file__).resolve().parents[1])
    for profile in store.profiles():print(profile['user'],normalize(store,profile['namespace'],profile['user']))
