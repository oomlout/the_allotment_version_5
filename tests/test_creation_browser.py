from io import BytesIO
from pathlib import Path

from PIL import Image,ImageDraw
from playwright.sync_api import expect

from test_studio_browser import studio
from allotment.images import RATIOS,digest

NS,USER='the_allotment','oomlout_main'


def sample():
    image=Image.new('RGB',(1000,700),'#dedbd1');draw=ImageDraw.Draw(image)
    draw.rectangle((80,80,520,620),fill='#278c85');draw.ellipse((580,140,900,460),fill='#e38042')
    draw.text((130,320),'ALLOTMENT IMAGE EDITOR',fill='white')
    data=BytesIO();image.save(data,'PNG');return data.getvalue()


def test_create_tag_edit_four_crops_reopen_and_choose_publish_version(studio):
    store,origin,browser=studio;page=browser.new_page(viewport={'width':1440,'height':1050});errors=[]
    page.on('pageerror',lambda error:errors.append(str(error)))
    base=origin+f'/g/{NS}/{USER}'
    page.goto(base+'/compose/')
    assert page.locator('[name="actions"]').count()==0
    page.get_by_label('Post text',exact=True).fill('Workshop image with URL https://example.com/')
    page.get_by_text('Add a tag',exact=True).click()
    page.locator('#new-tag-name').fill('Workshop');page.locator('#new-tag-group').fill('Subjects')
    page.get_by_role('button',name='Add tag',exact=True).click()
    expect(page.locator('#tag-message')).to_have_text('Tag added.')
    page.locator('input[name="media"]').set_input_files({'name':'workshop.png','mimeType':'image/png','buffer':sample()})
    editor=page.frame_locator('#image-editor-frame')
    expect(editor.locator('#image-editor')).to_have_attribute('data-ready','true')
    assert not store.posts(NS,USER) and not store.jobs(NS,USER)
    post=store.drafts(NS,USER)[0];folder=store.draft_dir(NS,USER,post['id']);source=folder/post['attachments'][0]['file'];before=digest(source)
    editor.get_by_role('button',name='Pop',exact=True).click()
    editor.get_by_text('Precise crop',exact=True).click()
    editor.locator('#crop-x').fill('150');editor.locator('#crop-width').fill('500');editor.locator('#crop-height').fill('500')
    editor.get_by_role('button',name='Apply coordinates').click()
    editor.get_by_role('button',name='4:5 · Portrait',exact=True).click()
    editor.get_by_role('button',name='Rotate right',exact=True).click()
    editor.get_by_role('button',name='1:1 · Square',exact=True).click()
    assert editor.locator('#adjust-contrast').input_value()=='112'
    editor.get_by_role('button',name='Save all four crops').click()
    expect(editor.locator('#image-status')).to_have_text('Four crop versions saved.')
    assert editor.locator('[data-saved]').count()==4
    post=store.draft(NS,USER,post['id']);variants=post['attachments'][0]['variants']
    assert set(variants)==set(RATIOS) and digest(source)==before
    assert variants['portrait']['recipe']['crop']['rotate']==90
    assert variants['square']['recipe']['adjustments']['contrast']==1.12
    for key,v in variants.items():
        with Image.open(folder/v['file']) as image:
            a,b=RATIOS[key];assert abs(image.width*b-image.height*a)<=max(a,b)
    evidence=Path(__file__).resolve().parents[1]/'.local/image-editor-work';evidence.mkdir(parents=True,exist_ok=True)
    page.screenshot(path=str(evidence/'editor-desktop.png'),full_page=True)
    page.reload();page.get_by_role('button',name='Edit image 1',exact=True).click();expect(editor.locator('#image-editor')).to_have_attribute('data-ready','true')
    assert editor.locator('#adjust-contrast').input_value()=='112'
    editor.get_by_role('button',name='4:5 · Portrait',exact=True).click()
    restored=editor.locator('#crop-source').evaluate('image=>image.cropper.getData()')
    for key in ('x','y','width','height','rotate'):
        assert abs(restored[key]-variants['portrait']['recipe']['crop'][key])<1,(key,restored,variants['portrait']['recipe']['crop'])
    editor.get_by_role('button',name='Save this crop',exact=True).click()
    expect(editor.locator('#image-status')).to_have_text('Crop saved.')
    page.set_viewport_size({'width':390,'height':844})
    expect(editor.locator('#crop-width')).to_have_value(str(round(restored['width'])),timeout=3000)
    assert page.evaluate('document.documentElement.scrollWidth<=innerWidth')
    page.screenshot(path=str(evidence/'editor-mobile.png'),full_page=True)
    page.get_by_role('button',name='Close editor',exact=True).click()
    expect(page.locator('#inline-image-editor')).to_be_hidden()
    assert page.locator('[name="tags"]:checked').count()==1
    page.get_by_role('button',name='Save & view post').click()
    assert not errors,errors
    page.get_by_role('button',name='Publish locally',exact=True).click()
    permalink=page.url
    page.get_by_text('Overrides for this post',exact=True).click()
    page.get_by_role('link',name='Edit platform defaults',exact=True).click()
    page.locator('select[name=frame_static]').select_option('square')
    page.get_by_role('button',name='Save defaults',exact=True).click()
    page.goto(permalink)
    assert page.locator('input[name=actions][value=static]').count()==0
    assert not store.jobs(NS,USER)
    page.get_by_role('button',name='Launch').click()
    expect(page.locator('#job-run-state')).to_have_text('Complete',timeout=15000)
    assert 'square-' in store.jobs(NS,USER)[0]['destination_posts']['static']['attachments'][0]['file']
    assert not errors
    page.close()


def test_tag_browser_search_rename_group_and_post_filter(studio):
    store,origin,browser=studio;page=browser.new_page();base=origin+f'/g/{NS}/{USER}'
    page.goto(base+'/tags/')
    page.get_by_label('Tag name',exact=True).fill('OOBB');page.get_by_label('Group',exact=True).fill('Projects');page.get_by_role('button',name='Add tag',exact=True).click()
    page.get_by_text('Edit tag',exact=True).click();page.get_by_label('Name',exact=True).fill('OOBB parts')
    page.locator('.tag-row input[name="group"]').fill('Systems');page.get_by_role('button',name='Save tag',exact=True).click()
    expect(page.get_by_role('heading',name='Systems',exact=True)).to_be_visible()
    page.get_by_label('Search tags and groups').fill('missing');assert page.locator('.tag-row:visible').count()==0
    page.get_by_label('Search tags and groups').fill('OOBB');assert page.locator('.tag-row:visible').count()==1
    page.locator('.tag-post-link').click();assert '?tag=' in page.url
    page.close()


def test_each_inline_crop_drags_independently_and_post_save_fetches_links(studio,monkeypatch):
    from allotment import links
    monkeypatch.setattr(links,'download',lambda url:(b'<title>Workshop repository</title><meta name="description" content="Build notes and files">',url))
    store,origin,browser=studio;page=browser.new_page(viewport={'width':1440,'height':1000})
    base=origin+f'/g/{NS}/{USER}'
    page.goto(base+'/compose/')
    page.get_by_label('Post text',exact=True).fill('A linked project')
    page.get_by_label('GitHub repository',exact=True).fill('https://github.com/oomlout/project')
    page.get_by_role('button',name='Add link',exact=True).click()
    page.get_by_label('Link 1',exact=True).fill('https://example.com/project')
    page.locator('input[name="media"]').set_input_files({'name':'project.png','mimeType':'image/png','buffer':sample()})
    editor=page.frame_locator('#image-editor-frame')
    expect(editor.locator('#image-editor')).to_have_attribute('data-ready','true')
    assert '/compose/' in page.url and '/images/' not in page.url
    crops={}
    for key in ('square','portrait','landscape','story'):
        editor.locator(f'[data-ratio="{key}"]').click()
        source=editor.locator('#crop-source')
        before=source.evaluate('image=>image.cropper.getData()')
        assert abs(before['x']-(1000-before['width'])/2)<2
        assert abs(before['y']-(700-before['height'])/2)<2
        face=editor.locator('.cropper-face');face.scroll_into_view_if_needed();box=face.bounding_box()
        x,y=box['x']+box['width']/2,box['y']+box['height']/2
        page.mouse.move(x,y);page.mouse.down();page.mouse.move(x+(0 if key=='landscape' else 20),y+(20 if key=='landscape' else 0),steps=8);page.mouse.up()
        after=source.evaluate('image=>image.cropper.getData()')
        assert abs(after['x']-before['x'])+abs(after['y']-before['y'])>2
        crops[key]=after
    page.get_by_role('button',name='Save draft',exact=True).click()
    expect(page.get_by_text('Draft saved locally.',exact=True)).to_be_visible()
    post=store.drafts(NS,USER)[0]
    assert len(post['links'])==2 and all(link['status']=='ready' for link in post['links'])
    assert page.locator('.link-card').count()==2
    variants=post['attachments'][0]['variants']
    for key in crops:
        for coordinate in ('x','y','width','height'):
            assert abs(variants[key]['recipe']['crop'][coordinate]-crops[key][coordinate])<2,(key,coordinate,variants[key]['recipe']['crop'],crops[key])
    assert not store.jobs(NS,USER) and not store.posts(NS,USER)
    page.close()
