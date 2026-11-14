"""Editable per-post platform copy. Importing copy never creates delivery evidence."""
from copy import deepcopy
import json
import re

from .storage import read_yaml,write_yaml,now
from .lifecycle import revision
from .translations import catalog,current_post,private_folder,load_result

STATUSES=('draft','needs_context','hold_quip_unavailable','hold_preparation_unavailable')
MAX_IMPORT=2*1024*1024


def path_for(store,ns,user,post_id):
    return private_folder(store,ns,user,post_id)/'platform-text.yaml'


def pending_path(store,ns,user,post_id):
    return private_folder(store,ns,user,post_id)/'pending-platform-text.yaml'


def selected_versions(store,ns,user,post):
    state=read_yaml(path_for(store,ns,user,post['id']))
    if not state.get('active'):return None
    if not state.get('posts'):return {}
    if state['source_revision']!=revision(post):
        raise ValueError('Platform text is out of date. Review and save it again, or choose Use original.')
    return deepcopy(state.get('posts',{}))


def deactivate(store,ns,user,post_id):
    path=path_for(store,ns,user,post_id);state=read_yaml(path)
    if state:write_yaml(path,{**state,'active':False})
    pending_path(store,ns,user,post_id).unlink(missing_ok=True)


def view(store,ns,user,post_id):
    post,_=current_post(store,ns,user,post_id)
    state=read_yaml(path_for(store,ns,user,post_id));entries=state.get('posts',{})
    selected=read_yaml(private_folder(store,ns,user,post_id)/'selection.yaml').get('run_id')
    source_revision=state.get('source_revision');enabled=bool(state.get('active') and entries)
    if not enabled and selected:
        run,result=load_result(store,ns,user,post_id,selected)
        entries=result['posts'];source_revision=run['source_revision'];enabled=True
    pending=read_yaml(pending_path(store,ns,user,post_id))
    if pending:
        entries=pending['posts'];source_revision=pending['source_revision']
    return {'entries':entries,'enabled':enabled,'revision':revision(post),
        'stale':bool((enabled or pending) and source_revision!=revision(post)),'statuses':STATUSES,
        'saved':bool(state.get('active')),'pending':bool(pending),'checked':bool(enabled or pending)}


def checked_entry(key,value,recipe):
    if not isinstance(value,dict):raise ValueError(f'{key}: expected a platform object.')
    message=value.get('message');title=value.get('title');tags=value.get('tags',[])
    if not isinstance(message,str) or not message.strip():raise ValueError(f'{key}: add text or use the original.')
    if not (title is None or isinstance(title,str)):raise ValueError(f'{key}: invalid title.')
    if key in {'medium','flickr'}:
        if not title or not title.strip():raise ValueError(f'{key}: a separate title is required.')
    elif title is not None:raise ValueError(f'{key}: this platform has no separate title field.')
    if not isinstance(tags,list) or any(not isinstance(t,str) or not re.fullmatch(r'[a-z0-9]+(?:_[a-z0-9]+)*',t) for t in tags):
        raise ValueError(f'{key}: use lowercase_underscore tags.')
    if key not in {'flickr','tumblr'} and tags:raise ValueError(f'{key}: put hashtags in the message.')
    if value.get('publication_status','not_published')!='not_published':
        raise ValueError('JSON cannot mark a platform as posted. Use the post’s review controls.')
    status=value.get('editorial_status','draft')
    if status not in STATUSES:raise ValueError(f'{key}: invalid editorial status.')
    ready=bool(recipe.get('preparation_ready',recipe.get('engine')=='oomlout_roboclick'))
    if not ready:status='hold_quip_unavailable' if key=='patreon' else 'hold_preparation_unavailable'
    payload=(title+'\n\n' if key=='medium' else '')+message
    if len(payload)>recipe.get('max_chars',10000):raise ValueError(f'{recipe["name"]}: text exceeds {recipe.get("max_chars",10000)} characters.')
    return {'title':title,'message':message,'tags':tags,'editorial_status':status,
        'publication_status':'not_published','strategy':value.get('strategy','') if isinstance(value.get('strategy',''),str) else '',
        'platform_name':recipe['name'],'repository_max_chars':recipe.get('max_chars',10000),'recipe_supports_preparation':ready}


def persist(store,ns,user,post,entries,**metadata):
    write_yaml(path_for(store,ns,user,post['id']),{'schema_version':'2.0','active':True,
        'source_revision':revision(post),'source':{'text':post['text']},'posts':entries,'saved_at':now(),**metadata})
    # The saved per-platform copy is now the explicit selection, rather than a
    # reference to an immutable AI run. The original AI evidence stays intact.
    write_yaml(private_folder(store,ns,user,post['id'])/'selection.yaml',{'run_id':'','reviewed_at':now()})
    pending_path(store,ns,user,post['id']).unlink(missing_ok=True)


def metadata_for(store,ns,user,post_id):
    pending=read_yaml(pending_path(store,ns,user,post_id))
    state=read_yaml(path_for(store,ns,user,post_id))
    if pending:return pending
    if state.get('active'):return state
    selected=read_yaml(private_folder(store,ns,user,post_id)/'selection.yaml').get('run_id')
    return load_result(store,ns,user,post_id,selected)[1] if selected else {}


def stage_result(store,ns,user,post_id,pack,source_revision):
    """Load captured/imported copy for editing, never select or approve it."""
    choices=catalog(store)
    entries={key:checked_entry(key,value,choices[key]) for key,value in pack['posts'].items()}
    write_yaml(pending_path(store,ns,user,post_id),{'posts':entries,'source_revision':source_revision,
        'source':pack['source'],'imported_run_id':pack.get('run_id'),'loaded_at':now(),
        'image':pack.get('image',{}),'questions':pack.get('questions',[]),'delivery_notes':pack.get('delivery_notes',[])})


def save(store,ns,user,post_id,form):
    with store.garden_lock(ns,user):
        post,_=current_post(store,ns,user,post_id);choices=catalog(store)
        if form.get('source_revision')!=revision(post):raise ValueError('The source post changed. Reload before saving platform text.')
        selected=form.getlist('platforms')
        if set(selected)-set(choices):raise ValueError('Unknown platform.')
        metadata=metadata_for(store,ns,user,post_id)
        entries={}
        for key in selected:
            entries[key]=checked_entry(key,{'message':form.get('message_'+key,''),
                'title':form.get('title_'+key,'') if key in {'medium','flickr'} else None,
                'tags':[t.strip() for t in re.split(r'[,\n]',form.get('tags_'+key,'')) if t.strip()],
                'editorial_status':form.get('status_'+key,'draft'),
                'strategy':metadata.get('posts',{}).get(key,{}).get('strategy','')},choices[key])
        persist(store,ns,user,post,entries,origin='manual',**{k:metadata[k] for k in ('image','questions','delivery_notes','imported_run_id') if k in metadata})


def parse_pack(raw):
    if len(raw.encode('utf-8') if isinstance(raw,str) else raw)>MAX_IMPORT:raise ValueError('JSON must be under 2 MB.')
    try:text=raw.decode('utf-8-sig') if isinstance(raw,bytes) else raw
    except UnicodeDecodeError as exc:raise ValueError('Upload UTF-8 JSON.') from exc
    text=text.strip()
    # Also accept the literal captured block, without any surrounding chat prose.
    tagged=re.fullmatch(r'<<<ALLOTMENT_TRANSLATION_([a-zA-Z0-9_-]+)>>>\s*(.*?)\s*<<<END_ALLOTMENT_TRANSLATION_\1>>>',text,re.S)
    if tagged:text=tagged[2]
    def unique(pairs):
        result={}
        for key,value in pairs:
            if key in result:raise ValueError('JSON contains a duplicate key.')
            result[key]=value
        return result
    def invalid_constant(value):raise ValueError('JSON contains an invalid number.')
    try:pack=json.loads(text,object_pairs_hook=unique,parse_constant=invalid_constant)
    except (json.JSONDecodeError,RecursionError) as exc:raise ValueError('Invalid JSON content pack.') from exc
    if not isinstance(pack,dict) or pack.get('schema_version')!='2.0':raise ValueError('Expected the pipeline’s schema_version 2.0 JSON.')
    if not isinstance(pack.get('image',{}),dict):raise ValueError('JSON image metadata must be an object.')
    for key in ('questions','delivery_notes'):
        if not isinstance(pack.get(key,[]),list) or any(not isinstance(v,str) for v in pack.get(key,[])):
            raise ValueError(f'JSON {key} must be a list of text.')
    return pack


def import_pack(store,ns,user,post_id,raw):
    pack=parse_pack(raw)
    with store.garden_lock(ns,user):
        post,_=current_post(store,ns,user,post_id);choices=catalog(store)
        if not isinstance(pack.get('source'),dict) or pack['source'].get('text')!=post['text']:
            raise ValueError('This JSON belongs to different source text. Open the matching post.')
        if pack.get('source_revision') and pack['source_revision']!=revision(post):
            raise ValueError('This JSON is for an older source revision. Generate versions again.')
        entries=pack.get('posts');destinations=pack.get('destinations')
        if not isinstance(entries,dict) or not entries or not isinstance(destinations,list) or any(not isinstance(k,str) for k in destinations) or len(destinations)!=len(set(destinations)) or set(entries)!=set(destinations):
            raise ValueError('JSON posts and destinations must contain the same platform IDs.')
        if set(entries)-set(choices):raise ValueError('JSON includes unavailable platform IDs: '+', '.join(sorted(set(entries)-set(choices))))
        checked={key:checked_entry(key,value,choices[key]) for key,value in entries.items()}
        # Import is a full pack replacement: omitted destinations use the source.
        # Validate everything before any mutation; a bad entry cannot partly load.
        stage_result(store,ns,user,post_id,pack,revision(post))
        return len(checked)


def export_pack(store,ns,user,post_id):
    from .translations import versions_for
    post,_=current_post(store,ns,user,post_id);entries=versions_for(store,ns,user,post)
    state=read_yaml(path_for(store,ns,user,post_id))
    if not state.get('active'):
        selected=read_yaml(private_folder(store,ns,user,post_id)/'selection.yaml').get('run_id')
        state=load_result(store,ns,user,post_id,selected)[1] if selected else {}
    return {'schema_version':'2.0','post_id':post_id,'source_revision':revision(post),'source':{'text':post['text']},
        'destinations':list(entries),'posts':entries,'image':state.get('image',{'alt_text':None}),
        'questions':state.get('questions',[]),'delivery_notes':state.get('delivery_notes',[])}
