"""Generate the manual Translate page only when its Guide link is requested."""
from html import escape
from uuid import uuid4
from flask import url_for
from .workflow_guide import render_yaml_guide
from .translations import GUIDE,catalog,current_post,make_prompt,private_folder,tags
from .translation_browser import RECIPE
from .lifecycle import revision
from .storage import read_yaml


def manual_guide(store,namespace,user,post_id=None):
    store.profile(namespace,user)
    inputs={
        'Browser':'Use your normal signed-in Chrome profile. Open a fresh ChatGPT tab for this task; leave other drafts untouched. The automated handoff gives 5 seconds to select Chrome, then snaps Win+Left and Win+Up, dismisses Snap Assist and counts down 8 seconds. For manual work, choose your window, dismiss Snap Assist if shown and keep browser zoom at 100%.',
        'url':'https://chatgpt.com/',
        'prompt_file / prompt':'Copy the complete post prompt below into a local UTF-8 prompt.txt file. This is the full source text, platform instructions, output schema and unique capture tags. Do not send the YAML itself as the prompt.',
        'images':'Attach each original image belonging to this post once. Use the image files listed below, not screenshots of the Studio page. Wait for all uploads to finish; thumbnails may be cropped.',
        'Upload checks':'Require one attachment removal control per image, no upload error or progress spinner, and an enabled Send control. Confirm this twice, two seconds apart. Do not compare thumbnail pixels or names.',
        'Loop values':'images means the original files listed here. image is the current file. chunks means consecutive pieces of the complete prompt, up to 2,500 characters each. uploaded_count is the number of images attached so far, not just this loop’s single image.',
        'Manual capture':'The file and HTML capture steps describe how automation reads the screen. When doing this manually, inspect the visible controls and copy the assistant’s completed tagged response. You do not need browser developer tools or an HTML export.',
        'Submission':'This workflow submits one translation prompt to ChatGPT only. It does not publish to social platforms. If the answer is slow, wait in the same chat; do not send the prompt again.',
        'Finish':'When the tagged response is complete, copy just the JSON between the matching markers. In the post page choose Platform text → Load JSON, review the loaded versions, then Save platform text. Loading a manual result does not approve any platform or mark anything posted.',
    }
    links=[];appendices={}
    if post_id:
        post,folder=current_post(store,namespace,user,post_id);choices=catalog(store)
        if any(a.get('type')!='image' for a in post.get('attachments',[])):
            raise ValueError('The translation guide currently supports image attachments only.')
        run={'id':'run-manual-'+uuid4().hex,'source':post,'source_revision':revision(post),
             'context':read_yaml(private_folder(store,namespace,user,post_id)/'context.yaml').get('context',''),
             'destinations':list(choices),'images':[str(folder/a['file']) for a in post.get('attachments',[])],
             'catalog':{k:{'name':v['name'],'max_chars':v.get('max_chars',10000),
                            'ready':bool(v.get('preparation_ready',v.get('engine')=='oomlout_roboclick'))} for k,v in choices.items()}}
        inputs['Post']=post['text']
        inputs['Image files']='\n'.join(run['images']) or 'No images for this post; skip the image loop.'
        inputs['Destinations']='This manual prompt includes all current social destinations. Held destinations stay held; it does not change your saved selection.'
        inputs['begin_tag'],inputs['end_tag']=tags(run)
        appendices['Complete prompt for this post — copy all text']=make_prompt(run,GUIDE.read_text(encoding='utf-8'))
        links.append(('Return to post',url_for('post_page',namespace=namespace,user=user,post_id=post_id)))
        for n,a in enumerate(post.get('attachments',[]),1):
            draft=(store.draft_dir(namespace,user,post_id)/'post.yaml').exists()
            href=url_for('draft_media',namespace=namespace,user=user,post_id=post_id,filename=a['file']) if draft else url_for('garden_media',namespace=namespace,user=user,filename=post['path']+'/'+a['file'])
            links.append((f'Open original image {n}',href))
    else:
        inputs['Post prompt']='Open this Guide from a post page to include that post’s complete prompt and image links. This general guide shows the procedure only.'
    links.append(('Download platform writing instructions',url_for('translation_instructions',namespace=namespace,user=user)))
    html=render_yaml_guide(RECIPE,title='Translate — manual steps',introduction=[
        'Follow the current Translate recipe manually. This page is generated only when you open Guide; opening it does not launch automation or change the post.',
        'If a check fails, stop and inspect the page. Keep the current chat open so you can see whether a prompt was already submitted before retrying.'
    ],inputs=inputs,appendices=appendices)
    navigation='<nav>'+ ' · '.join(f'<a href="{escape(url,quote=True)}">{escape(label)}</a>' for label,url in links)+'</nav>'
    return html.replace('<h1>',navigation+'<h1>',1)
