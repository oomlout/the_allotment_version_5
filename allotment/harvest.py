"""Read-only, bounded desktop harvesting. All HTML comes from keyboard clipboard capture."""
from pathlib import Path
from urllib.parse import urljoin,urlparse,unquote
import hashlib,json,re
from bs4 import BeautifulSoup
from .storage import read_yaml,write_yaml,now,slug
from .lifecycle import post_folder
from .browser import canonical_post_text


def recipe_for(platform):
    if platform!='bluesky':raise ValueError('Only Bluesky harvesting is implemented. Other platforms need checked read-only recipes.')
    recipe=read_yaml(Path(__file__).parent/'plugins/bluesky_harvest.yaml')
    if recipe.get('kind')!='harvest' or recipe.get('sending_enabled') is not False:raise ValueError('Harvesting must be read-only.')
    if set(recipe.get('expand_labels',[]))-{'Show more replies'}:raise ValueError('Unapproved expansion control. No input was made.')
    return recipe


def account_for(store,namespace,user,platform):
    account=store.settings(namespace,user).get('destinations',{}).get(platform,{})
    expected=account.get('expected_account','').lstrip('@')
    if not re.fullmatch(r'[a-zA-Z0-9.-]+',expected) or account.get('profile_url','').rstrip('/')!='https://bsky.app/profile/'+expected:
        raise ValueError('Set the exact Bluesky profile URL and expected handle in Sowing.')
    return account


def signature(store,namespace,user,platform):
    from .approval import signature as engine_signature
    recipe=recipe_for(platform);account=account_for(store,namespace,user,platform)
    data=engine_signature(store,recipe,account)+Path(__file__).read_text(encoding='utf-8')+(Path(__file__).parent/'job_runs.py').read_text(encoding='utf-8')
    return hashlib.sha256(data.encode()).hexdigest()


def approval_path(store,namespace,user,platform):
    return store.local/'harvest-approvals'/slug(namespace)/slug(user)/(slug(platform)+'.yaml')


def checked_test(store,namespace,user,run_id):
    from .job_runs import folder_for
    folder=folder_for(store,namespace,user,run_id);run=read_yaml(folder/'run.yaml');progress=read_yaml(folder/'progress.yaml')
    if not run.get('test') or run.get('kind')!='harvest' or progress.get('status')!='complete' or not progress.get('result',{}).get('verified') or (folder/'stop.flag').exists():raise ValueError('A successful read-only harvest test is required.')
    if run['signature']!=signature(store,namespace,user,run['platform']):raise ValueError('Harvest recipe, runtime or account changed. Test again.')
    return run


def approved(store,namespace,user,platform,mode):
    record=read_yaml(approval_path(store,namespace,user,platform)).get(mode,{})
    try:
        run=checked_test(store,namespace,user,record['run_id'])
        return run['mode']==mode and record['signature']==run['signature']
    except (ValueError,KeyError):return False


def approve(store,namespace,user,run_id):
    run=checked_test(store,namespace,user,run_id)
    path=approval_path(store,namespace,user,run['platform']);data=read_yaml(path)
    data[run['mode']]={'run_id':run_id,'signature':run['signature'],'approved_at':now()};write_yaml(path,data)
    return run


def overview(store,namespace,user,post_id):
    from .job_runs import runs_for
    folder=post_folder(store,namespace,user,post_id)/'harvest/bluesky'
    status={mode:approved(store,namespace,user,'bluesky',mode) for mode in ('ids','reactions')}
    tests=[]
    for run in runs_for(store,namespace,user,post_id):
        if run.get('kind')=='harvest' and run.get('test') and not status.get(run['mode']):
            try:checked_test(store,namespace,user,run['id'])
            except ValueError:continue
            if run['mode'] not in [r['mode'] for r in tests]:tests.append(run)
    return {'latest':read_yaml(folder/'id.yaml'),'reactions':read_yaml(folder/'latest.yaml'),'approved':status,'tests':tests}


def valid_url(url,base,author=None):
    parsed=urlparse(url)
    match=re.fullmatch(r'/profile/([^/]+)/post/([a-zA-Z0-9]+)',unquote(parsed.path).rstrip('/'))
    if parsed.scheme!=urlparse(base).scheme or parsed.netloc!=urlparse(base).netloc or parsed.query or parsed.fragment or not match:raise ValueError('Captured link is not a platform post URL.')
    if author and match[1].casefold()!=author.lstrip('@').casefold():raise ValueError('Captured post belongs to another author.')
    return {'url':url.rstrip('/'),'author':match[1],'post_id':match[2]}


def own_nodes(card,selector,card_selector):
    nodes=card.select(selector)
    blocked=card.select(card_selector)
    return [node for node in nodes if node not in blocked and not any(p in blocked for p in node.parents)]


def content_nodes(card,recipe):
    nodes=own_nodes(card,recipe['text'],recipe['profile_cards']+','+recipe['thread_cards']+','+recipe['exclude'])
    return [n for n in nodes if not any(p in nodes for p in n.parents)]


def card_urls(card,recipe,base,author=None):
    urls={}
    for anchor in own_nodes(card,'a[href]',recipe['profile_cards']+','+recipe['thread_cards']+','+recipe['exclude']):
        try:
            info=valid_url(urljoin(base,anchor['href']),base,author)
            urls[info['url']]=info
        except ValueError:pass
    return urls


def outer_cards(soup,selector):
    return [card for card in soup.select(selector) if not any(str(p.get('data-testid','')).startswith(('feedItem-by-','postThreadItem-by-')) for p in card.parents)]


def matching_cards(html,recipe,text,profile=False):
    if not text.strip():raise ValueError('Text-free posts need a media identity matcher before harvesting can be enabled.')
    soup=BeautifulSoup(html,'html.parser');selector=recipe['profile_cards'] if profile else recipe['thread_cards']
    return [card for card in outer_cards(soup,selector) if any(canonical_post_text(node,text)==' '.join(text.split()) for node in content_nodes(card,recipe))]


def discover(html,recipe,account,text):
    matches={}
    for card in matching_cards(html,recipe,text,True):
        # The outer card must belong to the expected author, not a quoted post.
        if card.get('data-testid')!='feedItem-by-'+account['expected_account'].lstrip('@'):continue
        urls=card_urls(card,recipe,account['profile_url'],account['expected_account'])
        if len(urls)==1:matches.update(urls)
    return matches


def metric(card,selector,recipe):
    for node in own_nodes(card,selector,recipe['profile_cards']+','+recipe['thread_cards']+','+recipe['exclude']):
        raw=node.get('aria-label') or node.get_text(' ',strip=True)
        found=re.search(r'(\d[\d,]*(?:\.\d+)?)\s*([KM])?',raw,re.I)
        if found:
            number=float(found[1].replace(',',''))*({'k':1000,'m':1000000}.get((found[2] or '').lower(),1))
            return {'value':int(number),'display':raw,'approximate':bool(found[2])}
    return None


def read_thread(html,recipe,root_url,text):
    soup=BeautifulSoup(html,'html.parser');base=recipe['base_url'];cards=soup.select(recipe['thread_cards'])
    roots=matching_cards(html,recipe,text)
    # URL and outer author must agree as well as exact content.
    identity=valid_url(root_url,base)
    roots=[c for c in roots if c.get('data-testid')=='postThreadItem-by-'+identity['author'] and (not card_urls(c,recipe,base) or root_url in card_urls(c,recipe,base))]
    if len(roots)!=1:raise ValueError('No unique matching post on the captured post page.')
    root=roots[0];counts={key:metric(root,selector,recipe) for key,selector in recipe['counts'].items()}
    return counts,collect_replies(soup,recipe,root_url),root.get_text(' ',strip=True)


def collect_replies(soup,recipe,root_url):
    replies={}
    for card in outer_cards(soup,recipe['thread_cards']):
        author=card.get('data-testid','').removeprefix('postThreadItem-by-')
        urls=card_urls(card,recipe,recipe['base_url'],author)
        if len(urls)!=1:continue
        url,identity=next(iter(urls.items()))
        if url==root_url:continue
        texts=content_nodes(card,recipe)
        replies[url]={**identity,'text':texts[0].get_text('',strip=False) if texts else '',
            'counts':{key:metric(card,selector,recipe) for key,selector in recipe['counts'].items()},
            'media':[{'url':n.get('src'),'alt':n.get('alt','')} for n in card.select('img[src]')],
            'links':[{'url':urljoin(recipe['base_url'],a['href']),'text':a.get_text(' ',strip=True)} for a in card.select('a[href]')],
            'timestamps':[{'datetime':n.get('datetime'),'text':n.get_text(' ',strip=True)} for n in card.select('time')],
            'captured_text':card.get_text(' ',strip=True)}
    return replies


def capture(session,name):
    session.check_stop()
    return Path(session.capture_page(name=name)).read_text(encoding='utf-8')


def inspect(session,recipe,account,text,mode='ids',known_url='',images=None):
    """Small read-only interpreter; native navigation, copy, scroll, and allowlisted expansion only."""
    session.navigate(account['profile_url'],seconds=3)
    session.check_owner(profile_url=account['profile_url'],expected_account=account['expected_account'],
        owner_text=recipe['owner_text'],owner_selector=recipe['owner_selector'],region=[0,.07,1,.9])
    candidates={}
    if known_url:candidates[known_url]=valid_url(known_url,recipe['base_url'],account['expected_account'])
    else:
        for index in range(min(int(recipe['profile_pages']),5)):
            candidates.update(discover(capture(session,'recent-'+str(index)),recipe,account,text))
            if index+1<recipe['profile_pages']:session.hotkey(['pagedown']);session.wait(1)
    if len(candidates)!=1:raise ValueError('Expected one exact match among recent posts; found '+str(len(candidates))+'. No ID was recorded.')
    identity=next(iter(candidates.values()));url=identity['url']
    session.navigate(url,seconds=3);html=capture(session,'post');counts,replies,root_text=read_thread(html,recipe,url,text)
    if images:
        cards=matching_cards(html,recipe,text)
        image_urls=[urljoin(url,n['src']) for card in cards for n in card.select('img[src]')]
        for image in images:session.check_image(image=image,expected_urls=image_urls,region=[0,.12,1,1],threshold=.82)
    result={**identity,'captured_at':now(),'verified':True,'sent':False,'text':text,'counts':counts,'replies':[],
        'scope':'post_id_only','captured_post_text':root_text,'limits':[]}
    if mode=='reactions':
        pending=[url];visited=set();expansions=0;captures=0
        while pending and len(visited)<min(int(recipe['max_pages']),50):
            current=pending.pop(0)
            if current in visited:continue
            visited.add(current)
            if current!=url:session.navigate(current,seconds=2)
            previous=None;unchanged=0
            for index in range(min(int(recipe['max_scrolls']),30)):
                html=capture(session,'thread-'+str(captures));captures+=1;soup=BeautifulSoup(html,'html.parser')
                fresh=collect_replies(soup,recipe,url)
                for reply_url,reply in fresh.items():
                    reply['observed_on']=sorted(set(replies.get(reply_url,{}).get('observed_on',[])+[current]))
                    replies[reply_url]=reply
                for a in soup.select(recipe['reply_links']):
                    try:target=valid_url(urljoin(current,a.get('href','')),recipe['base_url'])['url']
                    except ValueError:continue
                    if target not in visited and target not in pending:pending.append(target)
                expanded=False
                for label in recipe['expand_labels']:
                    controls=[n for n in soup.select('button, [role="button"]') if n.get_text(' ',strip=True)==label]
                    if len(controls)==1 and expansions<min(int(recipe['max_expansions']),50):
                        try:session.find_text(text=label,exact=True,timeout=.1,region=[0,.13,1,1])
                        except ValueError:continue
                        session.click_text(text=label,exact=True,timeout=.1,region=[0,.13,1,1]);session.wait(1);expansions+=1;expanded=True;break
                view=session.screenshot(name='thread-scroll-'+str(captures),region=[0,.13,1,1])
                pixels=Path(view['path']).read_bytes() if view else b''
                fingerprint=hashlib.sha256(soup.get_text().encode()+pixels).hexdigest()
                unchanged=unchanged+1 if fingerprint==previous and not expanded else 0;previous=fingerprint
                if unchanged>=2:break
                session.hotkey(['pagedown']);session.wait(1)
            else:result['limits'].append('Scroll limit reached on '+current)
        if pending:result['limits'].append('Reply branch limit reached')
        if expansions>=min(int(recipe['max_expansions']),50):result['limits'].append('Expansion limit reached')
        result.update(replies=list(replies.values()),scope='visible_thread_with_expanded_replies',pages_visited=sorted(visited),expansions=expansions,
            complete=False,availability='Only rendered information is available; hidden, deleted, unloaded or inaccessible replies and metrics may be absent.')
    session.screenshot(name='harvest-finished')
    return result


def run(store,namespace,user,run_data,check_stop):
    from .browser import browser_context
    from .job_runs import folder_for
    platform=run_data['platform'];mode=run_data['mode'];account=account_for(store,namespace,user,platform);recipe=recipe_for(platform)
    if run_data['signature']!=signature(store,namespace,user,platform):raise ValueError('Harvest configuration changed after queueing.')
    if not run_data['test'] and not approved(store,namespace,user,platform,mode):raise ValueError('Test and explicitly approve this harvest mode first.')
    evidence=folder_for(store,namespace,user,run_data['id'])/'evidence'
    with browser_context(store,evidence,check_stop=check_stop) as session:
        result=inspect(session,recipe,account,run_data['text'],mode,run_data.get('url',''),run_data.get('images',[]))
    check_stop()
    result.update(run_id=run_data['id'],source_revision=run_data['revision'])
    with store.garden_lock(namespace,user):
        folder=post_folder(store,namespace,user,run_data['post_id'])/'harvest'/platform
        if mode=='ids':
            previous=read_yaml(folder/'id.yaml')
            if previous and previous['url']!=result['url']:raise ValueError('A different post ID is already recorded. Review it before replacing it.')
            write_yaml(folder/'id.yaml',result)
        else:
            write_yaml(folder/'reactions'/(run_data['id']+'.yaml'),result);write_yaml(folder/'latest.yaml',result)
        write_yaml(evidence.parent/'result.yaml',result)
        # Only a verified public page can turn a manually prepared draft into a delivery receipt.
        from .receipts import save_job
        for job in store.jobs(namespace,user):
            receipt=job['actions'].get(platform,{})
            payload=job.get('destination_posts',{}).get(platform,job['post'])
            if job['post']['id']==run_data['post_id'] and receipt.get('status') in {'prepared','uncertain'} and payload['text']==run_data['text']:
                receipt.update(status='succeeded',url=result['url'],post_id=result['post_id'],verified=True,verification='read_only_harvest',harvest_run=run_data['id'],finished_at=now(),manual_submission_required=False,submitted_manually=True)
                save_job(store,namespace,user,job);break
    return result
