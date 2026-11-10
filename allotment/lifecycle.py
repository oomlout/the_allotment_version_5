"""Local publication state is independent of sowing to destinations."""
from datetime import datetime
import hashlib,json,shutil
from .storage import inside,now,write_yaml
from .images import media_files

def revision(post):
    return hashlib.sha256(json.dumps({key:post.get(key,[] if key in ('attachments','tags','links') else '') for key in ('text','attachments','tags','links','github_repo')},sort_keys=True).encode()).hexdigest()[:16]

def publish_local(store,namespace,user,post_id):
    with store.garden_lock(namespace,user):
        post=store.draft(namespace,user,post_id)
        if not post.get('text') and not post.get('attachments') and not post.get('links'):raise ValueError('Add text, media or links first.')
        old=store.published(namespace,user,post_id);draft=store.draft_dir(namespace,user,post_id)
        published_at=(old or {}).get('published_at') or now()
        path=(old or {}).get('path') or f'posts/{datetime.fromisoformat(published_at):%Y/%m/%d}/{post_id}'
        folder=inside(store.repo(namespace,user),path);folder.mkdir(parents=True,exist_ok=True)
        source=draft if (draft/'post.yaml').exists() else folder
        for attachment in post.get('attachments',[]):
            for filename in media_files(attachment):
                target=inside(folder,filename);target.parent.mkdir(parents=True,exist_ok=True)
                if inside(source,filename)!=target:shutil.copy2(inside(source,filename),target)
        if (source/'harvest').is_dir() and source!=folder:shutil.copytree(source/'harvest',folder/'harvest',dirs_exist_ok=True)
        post.update(status='published',published_at=published_at,revision=revision(post));post.pop('path',None)
        write_yaml(folder/'post.yaml',post)
        if source==draft:shutil.rmtree(draft)
        return post

def unpublish_local(store,namespace,user,post_id):
    with store.garden_lock(namespace,user):
        post=store.published(namespace,user,post_id)
        if not post:raise ValueError('This post is already unpublished.')
        source=inside(store.repo(namespace,user),post['path']);draft=store.draft_dir(namespace,user,post_id)
        if not (draft/'post.yaml').exists():
            shutil.copytree(source,draft,dirs_exist_ok=True)
            post.update(status='draft',updated_at=now());post.pop('path',None);write_yaml(draft/'post.yaml',post)
        elif (source/'harvest').is_dir():shutil.copytree(source/'harvest',draft/'harvest',dirs_exist_ok=True)
        shutil.rmtree(source)

def post_folder(store,namespace,user,post_id):
    post=store.published(namespace,user,post_id)
    if post:return inside(store.repo(namespace,user),post['path'])
    folder=store.draft_dir(namespace,user,post_id)
    if not (folder/'post.yaml').exists():raise ValueError('Post not found.')
    return folder
