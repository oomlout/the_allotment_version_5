"""User-owned tag catalogue. Stable IDs let names and groups change independently."""
from collections import Counter
import re
import uuid

from .storage import inside, read_yaml, slug, write_yaml


def catalogue(store, namespace, user):
    store.profile(namespace, user)
    return read_yaml(store.repo(namespace, user)/'tags.yaml', {'schema_version':1,'tags':[]})


def clean_label(value, limit=80):
    value=' '.join(str(value).strip().split())
    if not value or len(value)>limit:
        raise ValueError(f'Use a name between 1 and {limit} characters.')
    return value


def tag_name(value):
    return re.sub(r'[\s_]+','_',clean_label(value).lower())


def save_tag(store, namespace, user, name, group='Topics', tag_id=None):
    name,group=tag_name(name),clean_label(group or 'Topics')
    data=catalogue(store,namespace,user)
    duplicate=next((t for t in data['tags'] if t['name'].casefold()==name.casefold()),None)
    if duplicate and duplicate['id']!=tag_id:
        if tag_id:raise ValueError('That tag already exists. Use Merge to combine tags.')
        return duplicate
    if tag_id:
        tag=next((t for t in data['tags'] if t['id']==tag_id),None)
        if not tag:raise ValueError('Tag not found.')
    else:
        tag={'id':(re.sub('[^a-z0-9]+','-',name.lower()).strip('-')[:60] or 'tag')+'-'+uuid.uuid4().hex[:6]}
        data['tags'].append(tag)
    tag.update(name=name,group=group)
    data['tags'].sort(key=lambda t:(t['group'].casefold(),t['name'].casefold()))
    write_yaml(store.repo(namespace,user)/'tags.yaml',data)
    return tag


def validate_tags(store, namespace, user, values):
    allowed={t['id'] for t in catalogue(store,namespace,user)['tags']}
    values=list(dict.fromkeys(values))
    if len(values)>50 or any(t not in allowed for t in values):
        raise ValueError('Choose up to 50 existing tags.')
    return values


def post_files(store, namespace, user):
    return [*store.repo(namespace,user).glob('posts/*/*/*/*/post.yaml'),
            *(store.local/'drafts'/slug(namespace)/slug(user)).glob('*/post.yaml')]


def usage(store, namespace, user):
    posts={}
    for path in post_files(store,namespace,user):
        post=read_yaml(path)
        posts.setdefault(post['id'],set()).update(post.get('tags',[]))
    counts=Counter()
    for values in posts.values():counts.update(values)
    return counts


def merge_tags(store, namespace, user, source, target):
    data=catalogue(store,namespace,user)
    validate_tags(store,namespace,user,[source,target])
    if source==target:raise ValueError('Choose a different destination tag.')
    # Back up metadata, including the catalogue, before reorganising public posts.
    backup=store.local/'library-backups'/uuid.uuid4().hex
    for path in [store.repo(namespace,user)/'tags.yaml',*post_files(store,namespace,user)]:
        post=read_yaml(path)
        if path.name!='tags.yaml' and source not in post.get('tags',[]):continue
        copy=inside(backup,path.relative_to(store.root));copy.parent.mkdir(parents=True,exist_ok=True)
        copy.write_bytes(path.read_bytes())
        if path.name=='tags.yaml':continue
        post['tags']=list(dict.fromkeys(target if t==source else t for t in post['tags']))
        write_yaml(path,post)
    data['tags']=[t for t in data['tags'] if t['id']!=source]
    write_yaml(store.repo(namespace,user)/'tags.yaml',data)

